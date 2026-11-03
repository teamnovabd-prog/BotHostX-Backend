import os
from pathlib import PurePosixPath
import shutil
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.hosting_runtime.isolation import (
    TEXT_EDITABLE_EXTENSIONS,
    compute_sha256,
    get_isolated_staging_dir,
    parse_allowed_extensions,
    resolve_user_file,
    safe_extract_zip_bytes,
    sanitize_relative_path,
    validate_file_extension,
)
from app.models.models import HostingFileMetadata, HostingInstance, SystemSetting, User
from app.schemas.schemas import (
    CreateFolderRequest,
    ExtractZipRequest,
    FileContentUpdateRequest,
    FileItemResponse,
    RenameFileRequest,
)
from app.security.auth import get_current_user

router = APIRouter(prefix="/api/hosting/{hosting_id}", tags=["File Manager"])


def _verify_hosting_owner(db: Session, hosting_id: int, user: User) -> HostingInstance:
    hosting = db.query(HostingInstance).filter(HostingInstance.id == hosting_id).first()
    if hosting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hosting instance not found")
    if hosting.user_id != user.id and not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to another user's files")
    return hosting


def _refresh_storage_usage(db: Session, hosting: HostingInstance) -> None:
    total_bytes = (
        db.query(func.coalesce(func.sum(HostingFileMetadata.size_bytes), 0))
        .filter(HostingFileMetadata.hosting_id == hosting.id, HostingFileMetadata.is_directory.is_(False))
        .scalar()
        or 0
    )
    hosting.storage_used_mb = round(float(total_bytes) / (1024.0 * 1024.0), 2)


def _upsert_file_meta(
    db: Session,
    hosting_id: int,
    rel_path: str,
    file_type: str,
    is_dir: bool,
    size_bytes: int,
    sha256: str | None = None,
) -> HostingFileMetadata:
    posix = PurePosixPath(rel_path)
    parent_dir = "" if str(posix.parent) == "." else str(posix.parent)
    existing = (
        db.query(HostingFileMetadata)
        .filter(HostingFileMetadata.hosting_id == hosting_id, HostingFileMetadata.relative_path == rel_path)
        .first()
    )
    if existing:
        existing.size_bytes = size_bytes
        existing.sha256_hash = sha256
        return existing
    meta = HostingFileMetadata(
        hosting_id=hosting_id,
        parent_dir=parent_dir,
        relative_path=rel_path,
        file_name=posix.name,
        file_type=file_type,
        is_directory=is_dir,
        size_bytes=size_bytes,
        sha256_hash=sha256,
    )
    db.add(meta)
    return meta


@router.get("/files", response_model=List[FileItemResponse])
def list_files(
    hosting_id: int,
    directory: str = Query(default=""),
    search: Optional[str] = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    hosting = _verify_hosting_owner(db, hosting_id, current_user)
    clean_dir = sanitize_relative_path(directory, allow_empty=True)
    q = db.query(HostingFileMetadata).filter(HostingFileMetadata.hosting_id == hosting.id)
    if search and search.strip():
        q = q.filter(HostingFileMetadata.file_name.ilike(f"%{search.strip()}%"))
    else:
        q = q.filter(HostingFileMetadata.parent_dir == clean_dir)
    rows = q.order_by(HostingFileMetadata.is_directory.desc(), HostingFileMetadata.file_name.asc()).all()
    return [
        FileItemResponse(
            id=r.id,
            relative_path=r.relative_path,
            parent_dir=r.parent_dir,
            file_name=r.file_name,
            file_type=r.file_type,
            is_directory=r.is_directory,
            size_bytes=r.size_bytes,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


@router.post("/upload", response_model=FileItemResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(
    hosting_id: int,
    directory: str = Form(default=""),
    auto_extract_zip: bool = Form(default=False),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    hosting = _verify_hosting_owner(db, hosting_id, current_user)
    sys_cfg = db.query(SystemSetting).first()
    allowed_exts = parse_allowed_extensions(sys_cfg.allowed_file_types if sys_cfg else None)
    max_mb = sys_cfg.max_upload_size_mb if sys_cfg else 50

    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing uploaded file name")
    safe_name = os.path.basename(file.filename.strip())
    ext = validate_file_extension(safe_name, allowed_exts)

    content = await file.read()
    max_bytes = max_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {max_mb} MB upload limit",
        )

    quota_bytes = hosting.storage_limit_mb * 1024 * 1024
    current_bytes = int(hosting.storage_used_mb * 1024 * 1024)
    if current_bytes + len(content) > quota_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Insufficient hosting storage quota remaining for this upload",
        )

    clean_dir = sanitize_relative_path(directory, allow_empty=True)
    rel_path = f"{clean_dir}/{safe_name}".lstrip("/")
    dest_file = resolve_user_file(hosting.user_id, hosting.id, rel_path)
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    dest_file.write_bytes(content)

    meta = _upsert_file_meta(
        db=db,
        hosting_id=hosting.id,
        rel_path=rel_path,
        file_type=ext,
        is_dir=False,
        size_bytes=len(content),
        sha256=compute_sha256(content),
    )

    if ext == ".zip" and auto_extract_zip:
        target_dir = resolve_user_file(hosting.user_id, hosting.id, clean_dir, allow_root=True)
        extracted = safe_extract_zip_bytes(
            zip_bytes=content,
            dest_dir=target_dir,
            allowed_extensions=allowed_exts,
            max_total_uncompressed_bytes=max(0, quota_bytes - current_bytes),
        )
        for ext_rel, ext_size in extracted:
            full_rel = f"{clean_dir}/{ext_rel}".lstrip("/")
            base_n = os.path.basename(full_rel)
            e_type = ".env" if base_n == ".env" else os.path.splitext(base_n.lower())[1]
            _upsert_file_meta(db, hosting.id, full_rel, e_type, False, ext_size)

    db.flush()
    _refresh_storage_usage(db, hosting)
    db.commit()
    db.refresh(meta)
    return FileItemResponse(
        id=meta.id,
        relative_path=meta.relative_path,
        parent_dir=meta.parent_dir,
        file_name=meta.file_name,
        file_type=meta.file_type,
        is_directory=meta.is_directory,
        size_bytes=meta.size_bytes,
        updated_at=meta.updated_at,
    )


@router.post("/folder", response_model=FileItemResponse, status_code=status.HTTP_201_CREATED)
def create_folder(
    hosting_id: int,
    payload: CreateFolderRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    hosting = _verify_hosting_owner(db, hosting_id, current_user)
    rel_path = sanitize_relative_path(payload.folder_path)
    folder_fs = resolve_user_file(hosting.user_id, hosting.id, rel_path)
    folder_fs.mkdir(parents=True, exist_ok=True)
    meta = _upsert_file_meta(db, hosting.id, rel_path, "folder", True, 0)
    db.commit()
    db.refresh(meta)
    return FileItemResponse(
        id=meta.id,
        relative_path=meta.relative_path,
        parent_dir=meta.parent_dir,
        file_name=meta.file_name,
        file_type=meta.file_type,
        is_directory=meta.is_directory,
        size_bytes=0,
        updated_at=meta.updated_at,
    )


@router.put("/files/rename", response_model=FileItemResponse)
def rename_file(
    hosting_id: int,
    payload: RenameFileRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    hosting = _verify_hosting_owner(db, hosting_id, current_user)
    old_rel = sanitize_relative_path(payload.old_path)
    old_fs = resolve_user_file(hosting.user_id, hosting.id, old_rel)
    if not old_fs.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source file not found")

    meta = (
        db.query(HostingFileMetadata)
        .filter(HostingFileMetadata.hosting_id == hosting.id, HostingFileMetadata.relative_path == old_rel)
        .first()
    )
    if not meta:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File metadata record not found")

    safe_new_name = sanitize_relative_path(payload.new_name)
    if "/" in safe_new_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New name cannot contain directory separators")

    if not meta.is_directory:
        sys_cfg = db.query(SystemSetting).first()
        allowed_exts = parse_allowed_extensions(sys_cfg.allowed_file_types if sys_cfg else None)
        ext = validate_file_extension(safe_new_name, allowed_exts)
        meta.file_type = ext

    new_rel = f"{meta.parent_dir}/{safe_new_name}".lstrip("/")
    new_fs = resolve_user_file(hosting.user_id, hosting.id, new_rel)
    old_fs.rename(new_fs)

    meta.file_name = safe_new_name
    meta.relative_path = new_rel
    db.commit()
    db.refresh(meta)
    return FileItemResponse(
        id=meta.id,
        relative_path=meta.relative_path,
        parent_dir=meta.parent_dir,
        file_name=meta.file_name,
        file_type=meta.file_type,
        is_directory=meta.is_directory,
        size_bytes=meta.size_bytes,
        updated_at=meta.updated_at,
    )


@router.post("/files/extract")
def extract_zip_archive(
    hosting_id: int,
    payload: ExtractZipRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    hosting = _verify_hosting_owner(db, hosting_id, current_user)
    zip_rel = sanitize_relative_path(payload.zip_path)
    zip_fs = resolve_user_file(hosting.user_id, hosting.id, zip_rel)
    if not zip_fs.exists() or not zip_rel.lower().endswith(".zip"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Valid .zip archive not found")

    sys_cfg = db.query(SystemSetting).first()
    allowed_exts = parse_allowed_extensions(sys_cfg.allowed_file_types if sys_cfg else None)
    quota_bytes = hosting.storage_limit_mb * 1024 * 1024
    current_bytes = int(hosting.storage_used_mb * 1024 * 1024)

    parent_posix = PurePosixPath(zip_rel).parent
    parent_rel = "" if str(parent_posix) == "." else str(parent_posix)
    dest_dir = resolve_user_file(hosting.user_id, hosting.id, parent_rel, allow_root=True)

    extracted = safe_extract_zip_bytes(
        zip_bytes=zip_fs.read_bytes(),
        dest_dir=dest_dir,
        allowed_extensions=allowed_exts,
        max_total_uncompressed_bytes=max(0, quota_bytes - current_bytes),
    )

    for ext_rel, ext_size in extracted:
        full_rel = f"{parent_rel}/{ext_rel}".lstrip("/")
        parts = PurePosixPath(full_rel).parts
        for idx in range(1, len(parts)):
            folder_rel = "/".join(parts[:idx])
            _upsert_file_meta(db, hosting.id, folder_rel, "folder", True, 0)
        base_n = os.path.basename(full_rel)
        e_type = ".env" if base_n == ".env" else os.path.splitext(base_n.lower())[1]
        _upsert_file_meta(db, hosting.id, full_rel, e_type, False, ext_size)

    db.flush()
    _refresh_storage_usage(db, hosting)
    db.commit()
    return {"extracted_count": len(extracted), "message": f"Extracted {len(extracted)} files safely."}


@router.get("/files/content")
def read_text_file(
    hosting_id: int,
    path: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    hosting = _verify_hosting_owner(db, hosting_id, current_user)
    rel = sanitize_relative_path(path)
    target = resolve_user_file(hosting.user_id, hosting.id, rel)
    if not target.exists() or target.is_dir():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    base_n = os.path.basename(rel).lower()
    ext = ".env" if base_n == ".env" else os.path.splitext(base_n)[1]
    if ext not in TEXT_EDITABLE_EXTENSIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File type is binary and cannot be edited as text")

    return {"relative_path": rel, "content": target.read_text(encoding="utf-8", errors="replace")}


@router.put("/files/content")
def save_text_file(
    hosting_id: int,
    payload: FileContentUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    hosting = _verify_hosting_owner(db, hosting_id, current_user)
    rel = sanitize_relative_path(payload.relative_path)
    base_n = os.path.basename(rel).lower()
    ext = ".env" if base_n == ".env" else os.path.splitext(base_n)[1]
    if ext not in TEXT_EDITABLE_EXTENSIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported editable text file extension")

    encoded = payload.content.encode("utf-8")
    target = resolve_user_file(hosting.user_id, hosting.id, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(encoded)
    _upsert_file_meta(db, hosting.id, rel, ext, False, len(encoded), compute_sha256(encoded))
    db.flush()
    _refresh_storage_usage(db, hosting)
    db.commit()
    return {"message": "File saved successfully", "size_bytes": len(encoded)}


@router.get("/files/download")
def download_file(
    hosting_id: int,
    path: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    hosting = _verify_hosting_owner(db, hosting_id, current_user)
    rel = sanitize_relative_path(path)
    target = resolve_user_file(hosting.user_id, hosting.id, rel)
    if not target.exists() or target.is_dir():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    return FileResponse(path=str(target), filename=target.name)


@router.delete("/files")
def delete_file_or_folder(
    hosting_id: int,
    path: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    hosting = _verify_hosting_owner(db, hosting_id, current_user)
    rel = sanitize_relative_path(path)
    target = resolve_user_file(hosting.user_id, hosting.id, rel)
    if target == get_isolated_staging_dir(hosting.user_id, hosting.id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete root workspace")

    if target.exists():
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()

    (
        db.query(HostingFileMetadata)
        .filter(
            HostingFileMetadata.hosting_id == hosting.id,
            (HostingFileMetadata.relative_path == rel) | (HostingFileMetadata.relative_path.like(f"{rel}/%")),
        )
        .delete(synchronize_session=False)
    )
    db.flush()
    _refresh_storage_usage(db, hosting)
    db.commit()
    return {"message": f"Deleted '{rel}'"}
