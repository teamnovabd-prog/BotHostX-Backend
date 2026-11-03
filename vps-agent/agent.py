import base64
import hashlib
import hmac
import io
import os
from pathlib import Path
import platform
import shutil
import tarfile
import time
from typing import Any, Dict, Optional

import docker
from fastapi import FastAPI, Header, HTTPException, Request, status
import psutil
from pydantic import BaseModel

AGENT_SECRET_TOKEN = os.environ.get("VPS_AGENT_TOKEN", "").strip()
ISOLATED_HOSTING_ROOT = Path(os.environ.get("ISOLATED_HOSTING_ROOT", "/var/lib/smarthost/hosting")).resolve()
ISOLATED_NETWORK = os.environ.get("SMARTHOST_DOCKER_NETWORK", "smarthost_isolated_net")

app = FastAPI(title="SmartHost Secure VPS Agent", version="1.0.0")


async def verify_backend_signature(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_smarthost_timestamp: Optional[str] = Header(default=None),
    x_smarthost_signature: Optional[str] = Header(default=None),
) -> None:
    if not AGENT_SECRET_TOKEN or len(AGENT_SECRET_TOKEN) < 24:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VPS_AGENT_TOKEN must be configured with at least 24 random characters",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Bearer token")
    presented_token = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(presented_token, AGENT_SECRET_TOKEN):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid agent token")

    if not x_smarthost_timestamp or not x_smarthost_signature:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing request HMAC headers")
    try:
        ts_int = int(x_smarthost_timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed timestamp") from exc

    if abs(int(time.time()) - ts_int) > 120:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Expired request timestamp")

    body = await request.body()
    expected = hmac.new(
        AGENT_SECRET_TOKEN.encode("utf-8"),
        x_smarthost_timestamp.encode("utf-8") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, x_smarthost_signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="HMAC signature mismatch")


def _user_host_dir(user_id: int, hosting_id: int) -> Path:
    ISOLATED_HOSTING_ROOT.mkdir(parents=True, exist_ok=True)
    target = (ISOLATED_HOSTING_ROOT / f"user_{int(user_id)}" / f"host_{int(hosting_id)}").resolve()
    if ISOLATED_HOSTING_ROOT not in target.parents:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid isolated path")
    target.mkdir(parents=True, exist_ok=True)
    return target


def _safe_extract_tar_gz(tar_b64: str, target_dir: Path) -> None:
    raw = base64.b64decode(tar_b64.encode("ascii"))
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.issym() or member.islnk() or ".." in member.name or member.name.startswith("/"):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsafe archive entry rejected")
            dest = (target_dir / member.name).resolve()
            if dest != target_dir and target_dir not in dest.parents:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Path escape blocked")
        tar.extractall(path=str(target_dir), filter="data")


def _ensure_docker_network(dclient: docker.DockerClient) -> None:
    existing = [n.name for n in dclient.networks.list(names=[ISOLATED_NETWORK])]
    if ISOLATED_NETWORK not in existing:
        dclient.networks.create(
            ISOLATED_NETWORK,
            driver="bridge",
            options={"com.docker.network.bridge.enable_icc": "false"},
        )


@app.get("/v1/health")
async def agent_health(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_smarthost_timestamp: Optional[str] = Header(default=None),
    x_smarthost_signature: Optional[str] = Header(default=None),
):
    await verify_backend_signature(request, authorization, x_smarthost_timestamp, x_smarthost_signature)
    vm = psutil.virtual_memory()
    disk = shutil.disk_usage("/")
    net = psutil.net_io_counters()
    uptime_sec = int(time.time() - psutil.boot_time())

    docker_ok = False
    running_containers = 0
    hosting_count = 0
    try:
        dclient = docker.from_env()
        dclient.ping()
        docker_ok = True
        all_sh = dclient.containers.list(all=True, filters={"name": "sh_u"})
        hosting_count = len(all_sh)
        running_containers = sum(1 for c in all_sh if c.status == "running")
    except Exception:
        docker_ok = False

    return {
        "os_name": f"{platform.system()} {platform.release()}",
        "docker_available": docker_ok,
        "cpu_usage_percent": psutil.cpu_percent(interval=0.2),
        "ram_total_mb": int(vm.total // (1024 * 1024)),
        "ram_used_mb": int(vm.used // (1024 * 1024)),
        "disk_total_mb": int(disk.total // (1024 * 1024)),
        "disk_used_mb": int(disk.used // (1024 * 1024)),
        "network_rx_mb": round(net.bytes_recv / (1024 * 1024), 2),
        "network_tx_mb": round(net.bytes_sent / (1024 * 1024), 2),
        "uptime_seconds": uptime_sec,
        "running_containers": running_containers,
        "hosting_count": hosting_count,
    }


class ContainerControlPayload(BaseModel):
    action: str
    spec: Dict[str, Any]
    workspace_tar_gz_b64: Optional[str] = None


@app.post("/v1/containers/control")
async def control_container(
    payload: ContainerControlPayload,
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_smarthost_timestamp: Optional[str] = Header(default=None),
    x_smarthost_signature: Optional[str] = Header(default=None),
):
    await verify_backend_signature(request, authorization, x_smarthost_timestamp, x_smarthost_signature)
    spec = payload.spec
    user_id = int(spec["user_id"])
    hosting_id = int(spec["hosting_id"])
    container_name = str(spec["container_name"])
    host_dir = _user_host_dir(user_id, hosting_id)

    if payload.workspace_tar_gz_b64:
        _safe_extract_tar_gz(payload.workspace_tar_gz_b64, host_dir)

    dclient = docker.from_env()
    _ensure_docker_network(dclient)

    existing = None
    try:
        existing = dclient.containers.get(container_name)
    except docker.errors.NotFound:
        existing = None

    if payload.action == "sync":
        return {"verified": True, "message": "Workspace synchronized"}

    if payload.action == "stop":
        if existing:
            existing.stop(timeout=8)
        return {"verified": True, "message": f"Container {container_name} stopped"}

    if payload.action == "remove":
        if existing:
            existing.remove(force=True)
        shutil.rmtree(host_dir, ignore_errors=True)
        return {"verified": True, "message": f"Container {container_name} and isolated workspace removed"}

    if payload.action == "install_deps":
        req_file = host_dir / "requirements.txt"
        if not req_file.exists():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="requirements.txt not found in workspace")
        dclient.containers.run(
            image="python:3.12-slim",
            command=["pip", "install", "--target", "/workspace/.site-packages", "-r", "/workspace/requirements.txt"],
            volumes={str(host_dir): {"bind": "/workspace", "mode": "rw"}},
            remove=True,
            mem_limit=spec["limits"]["memory_bytes"],
            nano_cpus=spec["limits"]["nano_cpus"],
        )
        return {"verified": True, "message": "Installed requirements.txt packages into isolated .site-packages"}

    if payload.action in ("start", "restart"):
        if existing:
            existing.remove(force=True)

        hosting_type = str(spec["hosting_type"])
        mount_target = (
            "/var/www/html" if hosting_type == "php_script" else
            "/usr/share/nginx/html" if hosting_type == "html_web" else
            "/workspace"
        )
        env_map = dict(spec.get("env") or {})
        if hosting_type in ("python_bot", "telegram_bot"):
            env_map["PYTHONPATH"] = "/workspace:/workspace/.site-packages"

        limits = spec["limits"]
        labels = {
            "smarthost.managed": "true",
            "smarthost.user_id": str(user_id),
            "smarthost.hosting_id": str(hosting_id),
        }
        if spec.get("subdomain_slug"):
            labels["smarthost.subdomain"] = str(spec["subdomain_slug"])

        container = dclient.containers.run(
            image=str(spec["image"]),
            command=spec["command"],
            name=container_name,
            detach=True,
            working_dir=mount_target,
            environment=env_map,
            volumes={str(host_dir): {"bind": mount_target, "mode": "rw"}},
            nano_cpus=int(limits["nano_cpus"]),
            mem_limit=int(limits["memory_bytes"]),
            memswap_limit=int(limits["memory_bytes"]),
            pids_limit=int(limits["pids_limit"]),
            cap_drop=limits["cap_drop"],
            security_opt=limits["security_opt"],
            network=ISOLATED_NETWORK,
            restart_policy={"Name": "unless-stopped"},
            labels=labels,
        )
        return {
            "verified": True,
            "container_id": container.id[:12],
            "message": f"Container {container_name} is Running",
        }

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown action: {payload.action}")


@app.post("/v1/containers/logs")
async def container_logs(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_smarthost_timestamp: Optional[str] = Header(default=None),
    x_smarthost_signature: Optional[str] = Header(default=None),
):
    await verify_backend_signature(request, authorization, x_smarthost_timestamp, x_smarthost_signature)
    data = await request.json()
    cname = str(data.get("container_name", ""))
    tail = int(data.get("tail", 100))
    dclient = docker.from_env()
    try:
        c = dclient.containers.get(cname)
        stdout = c.logs(stdout=True, stderr=False, tail=tail).decode("utf-8", errors="replace")
        stderr = c.logs(stdout=False, stderr=True, tail=tail).decode("utf-8", errors="replace")
        return {"stdout": stdout, "stderr": stderr}
    except docker.errors.NotFound:
        return {"stdout": "", "stderr": ""}


@app.post("/v1/containers/export")
async def export_workspace(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_smarthost_timestamp: Optional[str] = Header(default=None),
    x_smarthost_signature: Optional[str] = Header(default=None),
):
    await verify_backend_signature(request, authorization, x_smarthost_timestamp, x_smarthost_signature)
    data = await request.json()
    host_dir = _user_host_dir(int(data["user_id"]), int(data["hosting_id"]))
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for item in host_dir.rglob("*"):
            if not item.is_symlink():
                tar.add(str(item), arcname=str(item.relative_to(host_dir)))
    return {"workspace_tar_gz_b64": base64.b64encode(buf.getvalue()).decode("ascii")}


@app.post("/v1/containers/import")
async def import_workspace(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_smarthost_timestamp: Optional[str] = Header(default=None),
    x_smarthost_signature: Optional[str] = Header(default=None),
):
    await verify_backend_signature(request, authorization, x_smarthost_timestamp, x_smarthost_signature)
    data = await request.json()
    spec = data["spec"]
    action = "start" if data.get("start_after") else "sync"
    payload = ContainerControlPayload(
        action=action,
        spec=spec,
        workspace_tar_gz_b64=data.get("workspace_tar_gz_b64"),
    )
    return await control_container(payload, request, authorization, x_smarthost_timestamp, x_smarthost_signature)
