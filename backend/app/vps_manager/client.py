import base64
import hashlib
import hmac
import io
import json
import tarfile
import time
from pathlib import Path
from typing import Any, Dict, Tuple

import httpx
import paramiko
from sqlalchemy.orm import Session

from app.models.models import VpsCredentialMetadata, VpsHealthCheck, VpsResourceMetric, VpsServer, utcnow
from app.security.crypto import decrypt_secret


class PinnedHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    """Strictly verifies SSH server host-key SHA256 fingerprint; rejects unverified hosts."""

    def __init__(self, expected_fingerprint: str | None) -> None:
        self.expected = (expected_fingerprint or "").strip()

    def missing_host_key(self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey) -> None:
        digest_b64 = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode("ascii").rstrip("=")
        actual_fp = f"SHA256:{digest_b64}"
        if not self.expected:
            raise paramiko.SSHException(
                f"SSH host key fingerprint required for host verification. Server presented {actual_fp}"
            )
        normalized_expected = self.expected if self.expected.startswith("SHA256:") else f"SHA256:{self.expected}"
        if not hmac.compare_digest(actual_fp, normalized_expected):
            raise paramiko.SSHException(
                f"Host key verification failed for {hostname}: expected {normalized_expected}, got {actual_fp}"
            )


class VpsManagementClient:
    """Executes verified, controlled operations on remote Linux VPS nodes via Secure Agent or SSH."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def _load_credentials(self, vps: VpsServer) -> VpsCredentialMetadata:
        cred = (
            self.db.query(VpsCredentialMetadata)
            .filter(VpsCredentialMetadata.vps_id == vps.id)
            .first()
        )
        if cred is None:
            raise RuntimeError("VPS credentials have not been configured yet.")
        return cred

    def _agent_headers(self, token: str, body_bytes: bytes) -> Dict[str, str]:
        ts = str(int(time.time()))
        msg = ts.encode("utf-8") + b"." + body_bytes
        sig = hmac.new(token.encode("utf-8"), msg, hashlib.sha256).hexdigest()
        return {
            "Authorization": f"Bearer {token}",
            "X-SmartHost-Timestamp": ts,
            "X-SmartHost-Signature": sig,
            "Content-Type": "application/json",
        }

    def _call_agent(self, vps: VpsServer, method: str, endpoint: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        cred = self._load_credentials(vps)
        raw_token = decrypt_secret(cred.encrypted_agent_token or cred.encrypted_secret)
        if not raw_token:
            raise RuntimeError("Missing decrypted VPS Agent token")
        base_url = (vps.agent_url or f"https://{vps.ip_address}:9443").rstrip("/")
        url = f"{base_url}{endpoint}"
        body_bytes = json.dumps(payload or {}, separators=(",", ":")).encode("utf-8") if method != "GET" else b""
        headers = self._agent_headers(raw_token, body_bytes)

        with httpx.Client(timeout=15.0, verify=True) as client:
            if method == "GET":
                resp = client.get(url, headers=headers)
            else:
                resp = client.request(method, url, content=body_bytes, headers=headers)
            resp.raise_for_status()
            return resp.json()

    def _ssh_probe(self, vps: VpsServer) -> Dict[str, Any]:
        cred = self._load_credentials(vps)
        secret = decrypt_secret(cred.encrypted_secret)
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(PinnedHostKeyPolicy(cred.host_key_fingerprint))
        try:
            connect_kwargs: Dict[str, Any] = {
                "hostname": vps.ip_address,
                "port": vps.ssh_port,
                "username": vps.ssh_username,
                "timeout": 12,
                "allow_agent": False,
                "look_for_keys": False,
            }
            if vps.auth_method == "ssh_key":
                pkey = paramiko.RSAKey.from_private_key(io.StringIO(secret))
                connect_kwargs["pkey"] = pkey
            else:
                connect_kwargs["password"] = secret

            ssh.connect(**connect_kwargs)
            # Controlled read-only telemetry probe — never interpolates user input
            probe_cmd = (
                "uname -sr && "
                "command -v docker >/dev/null 2>&1 && docker info --format '{{.ContainersRunning}}' || echo 'NO_DOCKER'; "
                "free -m | awk '/Mem:/ {print $2,$3}'; "
                "df -BM / | awk 'NR==2 {gsub(\"M\",\"\",$2); gsub(\"M\",\"\",$3); print $2,$3}'; "
                "awk '{print int($1)}' /proc/uptime"
            )
            _, stdout, _ = ssh.exec_command(probe_cmd, timeout=10)
            lines = [ln.strip() for ln in stdout.read().decode("utf-8", errors="replace").splitlines() if ln.strip()]
            os_detected = lines[0] if len(lines) > 0 else vps.os_name
            docker_line = lines[1] if len(lines) > 1 else "NO_DOCKER"
            docker_ok = docker_line != "NO_DOCKER"
            running_containers = int(docker_line) if docker_ok and docker_line.isdigit() else 0
            mem_parts = lines[2].split() if len(lines) > 2 else ["0", "0"]
            disk_parts = lines[3].split() if len(lines) > 3 else ["0", "0"]
            uptime_sec = int(lines[4]) if len(lines) > 4 and lines[4].isdigit() else 0
            return {
                "os_name": os_detected,
                "docker_available": docker_ok,
                "running_containers": running_containers,
                "ram_total_mb": int(mem_parts[0]),
                "ram_used_mb": int(mem_parts[1]),
                "disk_total_mb": int(disk_parts[0]),
                "disk_used_mb": int(disk_parts[1]),
                "cpu_usage_percent": 0.0,
                "uptime_seconds": uptime_sec,
            }
        finally:
            ssh.close()

    def verify_and_collect_health(self, vps: VpsServer) -> Tuple[bool, str]:
        """Performs a real connection & runtime health check; never marks Online unless probe succeeds."""
        started = time.perf_counter()
        vps.connection_status = "Connecting"
        self.db.commit()

        try:
            if vps.auth_method == "vps_agent":
                telemetry = self._call_agent(vps, "GET", "/v1/health")
            else:
                telemetry = self._ssh_probe(vps)

            latency_ms = round((time.perf_counter() - started) * 1000.0, 2)
            docker_ok = bool(telemetry.get("docker_available", False))
            os_detected = str(telemetry.get("os_name") or vps.os_name)

            vps.os_name = os_detected
            vps.docker_available = docker_ok
            vps.last_health_check_at = utcnow()

            if not vps.is_enabled:
                vps.connection_status = "Disabled"
                vps.last_error = None
            elif vps.maintenance_mode:
                vps.connection_status = "Maintenance"
                vps.last_error = None
            elif not docker_ok:
                vps.connection_status = "Setup Required"
                vps.last_error = "Connected to server, but Docker runtime is not installed or running."
            else:
                vps.connection_status = "Online"
                vps.last_error = None

            hc = VpsHealthCheck(
                vps_id=vps.id,
                status=vps.connection_status,
                latency_ms=latency_ms,
                docker_running=docker_ok,
                os_detected=os_detected,
                error_message=vps.last_error,
            )
            metric = VpsResourceMetric(
                vps_id=vps.id,
                cpu_usage_percent=float(telemetry.get("cpu_usage_percent", 0.0)),
                ram_used_mb=int(telemetry.get("ram_used_mb", 0)),
                disk_used_mb=int(telemetry.get("disk_used_mb", 0)),
                network_rx_mb=float(telemetry.get("network_rx_mb", 0.0)),
                network_tx_mb=float(telemetry.get("network_tx_mb", 0.0)),
                uptime_seconds=int(telemetry.get("uptime_seconds", 0)),
                running_containers=int(telemetry.get("running_containers", 0)),
                hosting_count=int(telemetry.get("hosting_count", 0)),
            )
            self.db.add(hc)
            self.db.add(metric)
            self.db.commit()
            return docker_ok, vps.connection_status

        except Exception as exc:
            latency_ms = round((time.perf_counter() - started) * 1000.0, 2)
            err_msg = str(exc)
            vps.connection_status = "Unreachable"
            vps.docker_available = False
            vps.last_error = err_msg[:500]
            vps.last_health_check_at = utcnow()
            self.db.add(
                VpsHealthCheck(
                    vps_id=vps.id,
                    status="Unreachable",
                    latency_ms=latency_ms,
                    docker_running=False,
                    os_detected=None,
                    error_message=vps.last_error,
                )
            )
            self.db.commit()
            return False, vps.last_error or "Connection failed"

    def sync_workspace_and_control_container(
        self,
        vps: VpsServer,
        action: str,
        spec: Dict[str, Any],
        staging_dir: Path | None = None,
    ) -> Dict[str, Any]:
        """Dispatches controlled container operations (start, stop, restart, remove, install_deps) to VPS Agent."""
        if vps.connection_status != "Online" or not vps.docker_available:
            raise RuntimeError(f"VPS '{vps.name}' is not Online with an active Docker runtime (status: {vps.connection_status})")

        archive_b64: str | None = None
        if staging_dir is not None and staging_dir.exists() and action in ("start", "restart", "install_deps", "sync"):
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                for item in staging_dir.rglob("*"):
                    if item.is_symlink():
                        continue
                    rel = item.relative_to(staging_dir)
                    tar.add(str(item), arcname=str(rel))
            archive_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

        payload = {
            "action": action,
            "spec": spec,
            "workspace_tar_gz_b64": archive_b64,
        }
        return self._call_agent(vps, "POST", "/v1/containers/control", payload)

    def fetch_container_logs(self, vps: VpsServer, container_name: str, tail: int = 200) -> Dict[str, Any]:
        if vps.connection_status != "Online":
            raise RuntimeError(f"VPS '{vps.name}' is currently {vps.connection_status}")
        return self._call_agent(
            vps,
            "POST",
            "/v1/containers/logs",
            {"container_name": container_name, "tail": tail},
        )

    def export_hosting_archive(self, vps: VpsServer, user_id: int, hosting_id: int) -> str:
        res = self._call_agent(
            vps,
            "POST",
            "/v1/containers/export",
            {"user_id": user_id, "hosting_id": hosting_id},
        )
        return str(res.get("workspace_tar_gz_b64", ""))

    def import_hosting_archive(self, vps: VpsServer, spec: Dict[str, Any], archive_b64: str, start_after: bool) -> Dict[str, Any]:
        return self._call_agent(
            vps,
            "POST",
            "/v1/containers/import",
            {"spec": spec, "workspace_tar_gz_b64": archive_b64, "start_after": start_after},
        )
