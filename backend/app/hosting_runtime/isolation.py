import hashlib
import io
import os
from pathlib import Path, PurePosixPath
from typing import Dict, List, Set, Tuple
import zipfile
from fastapi import HTTPException, status

from app.config import settings

TEXT_EDITABLE_EXTENSIONS: Set[str] = {
    ".py", ".php", ".html", ".css", ".js", ".json", ".txt", ".env"
}

DEFAULT_ALLOWED_EXTENSIONS: Set[str] = {
    ".py", ".php", ".html", ".css", ".js", ".json", ".txt", ".zip", ".env"
}


def parse_allowed_extensions(raw_csv: str | None) -> Set[str]:
    if not raw_csv:
        return set(DEFAULT_ALLOWED_EXTENSIONS)
    parsed = {item.strip().lower() for item in raw_csv.split(",") if item.strip()}
    return parsed or set(DEFAULT_ALLOWED_EXTENSIONS)


def sanitize_relative_path(raw_path: str, allow_empty: bool = False) -> str:
    cleaned = (raw_path or "").strip().replace("\\", "/")
    if "\x00" in cleaned:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Null bytes are prohibited in paths")
    cleaned = cleaned.lstrip("/")
    if not cleaned:
        if allow_empty:
            return ""
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File path cannot be empty")

    posix = PurePosixPath(cleaned)
    for part in posix.parts:
        if part in ("", ".", ".."):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Path traversal sequences are strictly prohibited",
            )
    return str(posix)


def get_isolated_staging_dir(user_id: int, hosting_id: int) -> Path:
    base = Path(settings.STAGING_ROOT).resolve()
    target = (base / f"user_{int(user_id)}" / f"host_{int(hosting_id)}").resolve()
    if not str(target).startswith(str(base)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid isolated path")
    target.mkdir(parents=True, exist_ok=True)
    return target


def resolve_user_file(user_id: int, hosting_id: int, relative_path: str, allow_root: bool = False) -> Path:
    root = get_isolated_staging_dir(user_id, hosting_id)
    rel = sanitize_relative_path(relative_path, allow_empty=allow_root)
    candidate = (root / rel).resolve() if rel else root
    if candidate != root and root not in candidate.parents:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Path escapes isolated hosting directory",
        )
    if candidate.is_symlink():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Symbolic links are not permitted")
    return candidate


def validate_file_extension(filename: str, allowed_extensions: Set[str]) -> str:
    lower_name = filename.strip().lower()
    if lower_name == ".env":
        ext = ".env"
    else:
        _, ext = os.path.splitext(lower_name)
    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(allowed_extensions))}",
        )
    return ext


def compute_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def safe_extract_zip_bytes(
    zip_bytes: bytes,
    dest_dir: Path,
    allowed_extensions: Set[str],
    max_total_uncompressed_bytes: int,
) -> List[Tuple[str, int]]:
    """Extracts a ZIP safely inside dest_dir, preventing zip-slip, symlinks, and zip bombs."""
    extracted_files: List[Tuple[str, int]] = []
    total_uncompressed = 0

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            members = zf.infolist()
            if len(members) > 500:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="ZIP archive contains too many entries (maximum 500 files)",
                )
            for info in members:
                if info.is_dir():
                    continue
                # Reject symlinks encoded in external_attr
                unix_mode = info.external_attr >> 16
                if (unix_mode & 0o170000) == 0o120000:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Symbolic links inside ZIP archives are prohibited",
                    )
                rel_path = sanitize_relative_path(info.filename)
                base_name = os.path.basename(rel_path)
                ext = ".env" if base_name == ".env" else os.path.splitext(base_name.lower())[1]
                if ext == ".zip" or ext not in allowed_extensions:
                    continue

                total_uncompressed += info.file_size
                if total_uncompressed > max_total_uncompressed_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Archive exceeds storage quota limit during extraction",
                    )

                out_path = (dest_dir / rel_path).resolve()
                if dest_dir not in out_path.parents:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Blocked ZIP path traversal attempt",
                    )
                out_path.parent.mkdir(parents=True, exist_ok=True)
                data = zf.read(info)
                out_path.write_bytes(data)
                extracted_files.append((rel_path, len(data)))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Corrupted or invalid ZIP file") from exc

    return extracted_files


def build_isolated_container_spec(
    user_id: int,
    hosting_id: int,
    hosting_type: str,
    cpu_limit_cores: float,
    ram_limit_mb: int,
    storage_limit_mb: int,
    entry_file: str,
    subdomain_slug: str | None,
    env_vars: Dict[str, str],
) -> Dict[str, object]:
    """Builds a controlled, least-privilege Docker runtime specification sent to the VPS Agent."""
    safe_entry = sanitize_relative_path(entry_file)
    container_name = f"sh_u{int(user_id)}_h{int(hosting_id)}"

    if hosting_type in ("python_bot", "telegram_bot"):
        image = "python:3.12-slim"
        command = ["python", "-u", f"/workspace/{safe_entry}"]
        internal_port = None
    elif hosting_type == "php_script":
        image = "php:8.3-apache"
        command = ["apache2-foreground"]
        internal_port = 80
    elif hosting_type == "html_web":
        image = "nginx:1.27-alpine"
        command = ["nginx", "-g", "daemon off;"]
        internal_port = 80
    else:
        raise ValueError(f"Unsupported hosting_type: {hosting_type}")

    return {
        "container_name": container_name,
        "user_id": int(user_id),
        "hosting_id": int(hosting_id),
        "hosting_type": hosting_type,
        "image": image,
        "command": command,
        "internal_port": internal_port,
        "subdomain_slug": subdomain_slug,
        "limits": {
            "nano_cpus": int(cpu_limit_cores * 1_000_000_000),
            "memory_bytes": int(ram_limit_mb) * 1024 * 1024,
            "storage_quota_mb": int(storage_limit_mb),
            "pids_limit": 64,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "network_mode": "smarthost_isolated_net",
        },
        "env": env_vars,
    }
