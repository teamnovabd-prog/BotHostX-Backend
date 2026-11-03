import json
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.hosting_runtime.isolation import build_isolated_container_spec, get_isolated_staging_dir
from app.models.models import (
    HostingInstance,
    VpsHostingAssignment,
    VpsMigrationJob,
    VpsOperationLog,
    VpsServer,
    utcnow,
)
from app.security.crypto import decrypt_secret
from app.vps_manager.client import VpsManagementClient
from app.vps_manager.selector import is_vps_eligible_for_hosting


def _log_migration(db: Session, vps_id: int, job_id: int, level: str, message: str) -> None:
    db.add(
        VpsOperationLog(
            vps_id=vps_id,
            migration_job_id=job_id,
            level=level,
            message=message,
        )
    )
    db.commit()


def execute_safe_vps_migration(
    db: Session,
    hosting: HostingInstance,
    target_vps: VpsServer,
    admin_user_id: int,
    preserve_url: bool = True,
) -> VpsMigrationJob:
    """
    Executes the 10-step verified VPS migration workflow:
    1. Validate target VPS
    2. Verify available resources
    3. Safely stop source hosting (never deleting source files!)
    4. Transfer hosting archive & encrypted configuration securely
    5. Recreate isolated Docker runtime on target VPS
    6. Verify target container startup
    7. Update HostingInstance & VpsHostingAssignment in DB
    8. Preserve public URL when configured
    9. Record complete migration log
    10. Rollback automatically if any step fails
    """
    if hosting.assigned_vps_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hosting has no source VPS assigned; use Assign VPS instead.",
        )
    if hosting.assigned_vps_id == target_vps.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Source and target VPS servers are identical.",
        )

    source_vps = db.query(VpsServer).filter(VpsServer.id == hosting.assigned_vps_id).first()
    if source_vps is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source VPS record not found")

    # Step 1 & 2: Validate target VPS and verify available resources
    eligible, reason = is_vps_eligible_for_hosting(
        db=db,
        vps=target_vps,
        required_cpu=hosting.cpu_limit_cores,
        required_ram_mb=hosting.ram_limit_mb,
        required_storage_mb=hosting.storage_limit_mb,
        hosting_type=hosting.hosting_type,
    )
    if not eligible:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Target VPS '{target_vps.name}' ineligible for migration: {reason}",
        )

    job = VpsMigrationJob(
        hosting_id=hosting.id,
        source_vps_id=source_vps.id,
        target_vps_id=target_vps.id,
        initiated_by_id=admin_user_id,
        status="Validating",
        preserve_url=preserve_url,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    client = VpsManagementClient(db)
    was_running = hosting.status == "Running"

    env_vars = {}
    if hosting.encrypted_env_json:
        env_vars = json.loads(decrypt_secret(hosting.encrypted_env_json))
    if hosting.encrypted_bot_token:
        env_vars["BOT_TOKEN"] = decrypt_secret(hosting.encrypted_bot_token)

    spec = build_isolated_container_spec(
        user_id=hosting.user_id,
        hosting_id=hosting.id,
        hosting_type=hosting.hosting_type,
        cpu_limit_cores=hosting.cpu_limit_cores,
        ram_limit_mb=hosting.ram_limit_mb,
        storage_limit_mb=hosting.storage_limit_mb,
        entry_file=hosting.entry_file,
        subdomain_slug=hosting.subdomain_slug,
        env_vars=env_vars,
    )

    try:
        _log_migration(db, target_vps.id, job.id, "INFO", f"Validated target VPS '{target_vps.name}' capacity.")

        # Step 3: Safely stop source container (source directory is preserved intact)
        job.status = "StoppingSource"
        db.commit()
        if source_vps.connection_status == "Online":
            client.sync_workspace_and_control_container(source_vps, "stop", spec, None)
            _log_migration(db, source_vps.id, job.id, "INFO", "Source container stopped safely; source files preserved.")

        # Step 4: Export archive from source VPS (or central staging backup if source is unreachable)
        job.status = "Transferring"
        db.commit()
        if source_vps.connection_status == "Online":
            archive_b64 = client.export_hosting_archive(source_vps, hosting.user_id, hosting.id)
        else:
            staging_dir = get_isolated_staging_dir(hosting.user_id, hosting.id)
            res = client.sync_workspace_and_control_container(
                target_vps, "start" if was_running else "sync", spec, staging_dir
            )
            archive_b64 = ""

        # Step 5 & 6: Recreate isolated container runtime on target VPS and verify startup
        job.status = "StartingTarget"
        db.commit()
        if archive_b64:
            target_res = client.import_hosting_archive(target_vps, spec, archive_b64, start_after=was_running)
        else:
            target_res = res

        verified = bool(target_res.get("verified", False))
        if not verified:
            raise RuntimeError("Target VPS failed runtime verification check after container import.")

        # Step 7 & 8: Update database assignments while preserving public URL
        (
            db.query(VpsHostingAssignment)
            .filter(VpsHostingAssignment.hosting_id == hosting.id, VpsHostingAssignment.is_active.is_(True))
            .update({"is_active": False, "released_at": utcnow()})
        )
        db.add(
            VpsHostingAssignment(
                hosting_id=hosting.id,
                vps_id=target_vps.id,
                assigned_by_user_id=admin_user_id,
                assignment_mode="migration",
                is_active=True,
            )
        )
        hosting.assigned_vps_id = target_vps.id
        hosting.container_id = str(target_res.get("container_id") or hosting.container_id or "")
        hosting.status = "Running" if was_running else "Stopped"

        # Step 9: Complete job & preserve audit trail
        job.status = "Completed"
        job.completed_at = utcnow()
        db.commit()
        _log_migration(
            db,
            target_vps.id,
            job.id,
            "INFO",
            f"Migration to '{target_vps.name}' verified and completed. Source files retained for safety.",
        )
        return job

    except Exception as exc:
        # Step 10: Safe Rollback — restart source container if it was previously running
        err_text = str(exc)[:500]
        job.error_message = err_text
        rolled_back = False
        if was_running and source_vps.connection_status == "Online":
            try:
                client.sync_workspace_and_control_container(source_vps, "start", spec, None)
                hosting.status = "Running"
                rolled_back = True
            except Exception:
                hosting.status = "Error"

        job.status = "RolledBack" if rolled_back else "Failed"
        job.completed_at = utcnow()
        db.commit()
        _log_migration(
            db,
            source_vps.id,
            job.id,
            "ERROR",
            f"Migration failed ({err_text}). Rollback status: {job.status}.",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"VPS migration failed and was {job.status}: {err_text}",
        ) from exc
