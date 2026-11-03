from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.models import (
    HostingInstance,
    User,
    VpsCredentialMetadata,
    VpsHostingAssignment,
    VpsMigrationJob,
    VpsOperation,
    VpsOperationLog,
    VpsResourceMetric,
    VpsServer,
    utcnow,
)
from app.schemas.schemas import (
    AssignVpsRequest,
    HostingResponse,
    MaintenanceToggleRequest,
    MigrateVpsRequest,
    VpsClusterSummaryResponse,
    VpsCreateUpdateRequest,
    VpsResponse,
)
from app.security.auth import require_admin
from app.security.crypto import encrypt_secret
from app.services.serializers import build_hosting_response, build_vps_cluster_summary, build_vps_response
from app.vps_manager.client import VpsManagementClient
from app.vps_manager.migrator import execute_safe_vps_migration
from app.vps_manager.selector import is_vps_eligible_for_hosting

router = APIRouter(prefix="/api/admin", tags=["Admin Multi-VPS Management"])


def _record_vps_op(db: Session, vps_id: int, admin_id: int, op_type: str, status_val: str, summary: str) -> None:
    op = VpsOperation(
        vps_id=vps_id,
        initiated_by_id=admin_id,
        operation_type=op_type,
        status=status_val,
        summary=summary,
        completed_at=utcnow(),
    )
    db.add(op)
    db.flush()
    db.add(
        VpsOperationLog(
            vps_id=vps_id,
            operation_id=op.id,
            level="INFO" if status_val == "Completed" else "ERROR",
            message=summary,
        )
    )


@router.get("/vps/summary", response_model=VpsClusterSummaryResponse)
def get_vps_summary(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return build_vps_cluster_summary(db)


@router.get("/vps", response_model=List[VpsResponse])
def list_vps_servers(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    servers = db.query(VpsServer).order_by(VpsServer.created_at.desc()).all()
    return [build_vps_response(db, s) for s in servers]


@router.post("/vps", response_model=VpsResponse, status_code=status.HTTP_201_CREATED)
def add_vps_server(
    payload: VpsCreateUpdateRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if db.query(VpsServer).filter(VpsServer.name == payload.name.strip()).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A VPS with this name already exists")

    # Never assume a newly added VPS is Online until a real health check succeeds!
    vps = VpsServer(
        name=payload.name.strip(),
        provider=payload.provider.strip(),
        ip_address=payload.ip_address.strip(),
        ssh_port=payload.ssh_port,
        ssh_username=payload.ssh_username.strip(),
        auth_method=payload.auth_method,
        agent_url=payload.agent_url.strip() if payload.agent_url else None,
        os_name=payload.os_name.strip(),
        region=payload.region.strip(),
        cpu_capacity_cores=payload.cpu_capacity_cores,
        ram_capacity_mb=payload.ram_capacity_mb,
        storage_capacity_mb=payload.storage_capacity_mb,
        reserved_cpu_cores=payload.reserved_cpu_cores,
        reserved_ram_mb=payload.reserved_ram_mb,
        reserved_storage_mb=payload.reserved_storage_mb,
        max_hosting_count=payload.max_hosting_count,
        runtime_type=payload.runtime_type.strip(),
        connection_status="Setup Required",
        docker_available=False,
        is_enabled=payload.is_enabled,
        maintenance_mode=payload.maintenance_mode,
    )
    db.add(vps)
    db.flush()

    if payload.secret_credential or payload.agent_token:
        db.add(
            VpsCredentialMetadata(
                vps_id=vps.id,
                credential_type=payload.auth_method,
                encrypted_secret=encrypt_secret(payload.secret_credential or payload.agent_token or ""),
                encrypted_agent_token=encrypt_secret(payload.agent_token) if payload.agent_token else None,
                host_key_fingerprint=payload.host_key_fingerprint.strip() if payload.host_key_fingerprint else None,
                key_label=f"{vps.name}-cred",
            )
        )

    _record_vps_op(
        db,
        vps.id,
        admin.id,
        "register_vps",
        "Completed",
        f"Registered VPS '{vps.name}' ({vps.ip_address}). Run Test Connection to verify Docker runtime.",
    )
    db.commit()
    db.refresh(vps)
    return build_vps_response(db, vps)


@router.get("/vps/{vps_id}", response_model=VpsResponse)
def get_vps_detail(vps_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    vps = db.query(VpsServer).filter(VpsServer.id == vps_id).first()
    if not vps:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VPS server not found")
    return build_vps_response(db, vps)


@router.put("/vps/{vps_id}", response_model=VpsResponse)
def update_vps_server(
    vps_id: int,
    payload: VpsCreateUpdateRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    vps = db.query(VpsServer).filter(VpsServer.id == vps_id).first()
    if not vps:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VPS server not found")

    vps.name = payload.name.strip()
    vps.provider = payload.provider.strip()
    vps.ip_address = payload.ip_address.strip()
    vps.ssh_port = payload.ssh_port
    vps.ssh_username = payload.ssh_username.strip()
    vps.auth_method = payload.auth_method
    vps.agent_url = payload.agent_url.strip() if payload.agent_url else None
    vps.os_name = payload.os_name.strip()
    vps.region = payload.region.strip()
    vps.cpu_capacity_cores = payload.cpu_capacity_cores
    vps.ram_capacity_mb = payload.ram_capacity_mb
    vps.storage_capacity_mb = payload.storage_capacity_mb
    vps.reserved_cpu_cores = payload.reserved_cpu_cores
    vps.reserved_ram_mb = payload.reserved_ram_mb
    vps.reserved_storage_mb = payload.reserved_storage_mb
    vps.max_hosting_count = payload.max_hosting_count
    vps.runtime_type = payload.runtime_type.strip()
    vps.is_enabled = payload.is_enabled
    vps.maintenance_mode = payload.maintenance_mode

    if payload.secret_credential or payload.agent_token or payload.host_key_fingerprint:
        cred = db.query(VpsCredentialMetadata).filter(VpsCredentialMetadata.vps_id == vps.id).first()
        if cred is None:
            cred = VpsCredentialMetadata(
                vps_id=vps.id,
                credential_type=payload.auth_method,
                encrypted_secret=encrypt_secret(payload.secret_credential or payload.agent_token or ""),
            )
            db.add(cred)
        else:
            cred.credential_type = payload.auth_method
            if payload.secret_credential:
                cred.encrypted_secret = encrypt_secret(payload.secret_credential)
            if payload.agent_token:
                cred.encrypted_agent_token = encrypt_secret(payload.agent_token)
            if payload.host_key_fingerprint is not None:
                cred.host_key_fingerprint = payload.host_key_fingerprint.strip()
            cred.rotated_at = utcnow()

    _record_vps_op(db, vps.id, admin.id, "update_vps", "Completed", f"Updated VPS '{vps.name}' metadata.")
    db.commit()
    db.refresh(vps)
    return build_vps_response(db, vps)


@router.delete("/vps/{vps_id}")
def remove_vps_server(vps_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    vps = db.query(VpsServer).filter(VpsServer.id == vps_id).first()
    if not vps:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VPS server not found")

    assigned_count = (
        db.query(func.count(HostingInstance.id))
        .filter(HostingInstance.assigned_vps_id == vps.id)
        .scalar()
        or 0
    )
    if assigned_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot remove VPS '{vps.name}': {assigned_count} hosting instance(s) are still assigned. Migrate or remove assigned hosting first.",
        )

    db.delete(vps)
    db.commit()
    return {"message": f"VPS '{vps.name}' safely removed."}


@router.post("/vps/{vps_id}/test-connection", response_model=VpsResponse)
def test_vps_connection(vps_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    vps = db.query(VpsServer).filter(VpsServer.id == vps_id).first()
    if not vps:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VPS server not found")
    client = VpsManagementClient(db)
    ok, msg = client.verify_and_collect_health(vps)
    _record_vps_op(
        db,
        vps.id,
        admin.id,
        "test_connection",
        "Completed" if ok else "Failed",
        f"Health verification result: {msg}",
    )
    db.commit()
    db.refresh(vps)
    return build_vps_response(db, vps)


@router.post("/vps/{vps_id}/connect", response_model=VpsResponse)
def connect_vps_server(vps_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return test_vps_connection(vps_id, admin, db)


@router.post("/vps/{vps_id}/disconnect", response_model=VpsResponse)
def disconnect_vps_server(vps_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    vps = db.query(VpsServer).filter(VpsServer.id == vps_id).first()
    if not vps:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VPS server not found")
    vps.connection_status = "Offline"
    _record_vps_op(db, vps.id, admin.id, "disconnect", "Completed", f"Admin disconnected VPS '{vps.name}'.")
    db.commit()
    db.refresh(vps)
    return build_vps_response(db, vps)


@router.post("/vps/{vps_id}/enable", response_model=VpsResponse)
def enable_vps_server(vps_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    vps = db.query(VpsServer).filter(VpsServer.id == vps_id).first()
    if not vps:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VPS server not found")
    vps.is_enabled = True
    if vps.connection_status == "Disabled":
        vps.connection_status = "Setup Required"
    _record_vps_op(db, vps.id, admin.id, "enable", "Completed", f"Enabled VPS '{vps.name}'.")
    db.commit()
    db.refresh(vps)
    return build_vps_response(db, vps)


@router.post("/vps/{vps_id}/disable", response_model=VpsResponse)
def disable_vps_server(vps_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    vps = db.query(VpsServer).filter(VpsServer.id == vps_id).first()
    if not vps:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VPS server not found")
    vps.is_enabled = False
    vps.connection_status = "Disabled"
    _record_vps_op(db, vps.id, admin.id, "disable", "Completed", f"Disabled VPS '{vps.name}'.")
    db.commit()
    db.refresh(vps)
    return build_vps_response(db, vps)


@router.post("/vps/{vps_id}/maintenance", response_model=VpsResponse)
def set_vps_maintenance(
    vps_id: int,
    payload: MaintenanceToggleRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    vps = db.query(VpsServer).filter(VpsServer.id == vps_id).first()
    if not vps:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VPS server not found")
    vps.maintenance_mode = payload.maintenance_mode
    if payload.maintenance_mode:
        vps.connection_status = "Maintenance"
    elif vps.docker_available:
        vps.connection_status = "Online"
    else:
        vps.connection_status = "Setup Required"
    _record_vps_op(
        db,
        vps.id,
        admin.id,
        "maintenance",
        "Completed",
        f"Set maintenance_mode={payload.maintenance_mode} on VPS '{vps.name}'.",
    )
    db.commit()
    db.refresh(vps)
    return build_vps_response(db, vps)


@router.get("/vps/{vps_id}/metrics")
def get_vps_metrics_history(vps_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    metrics = (
        db.query(VpsResourceMetric)
        .filter(VpsResourceMetric.vps_id == vps_id)
        .order_by(VpsResourceMetric.recorded_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "id": m.id,
            "cpu_usage_percent": m.cpu_usage_percent,
            "ram_used_mb": m.ram_used_mb,
            "disk_used_mb": m.disk_used_mb,
            "network_rx_mb": m.network_rx_mb,
            "network_tx_mb": m.network_tx_mb,
            "uptime_seconds": m.uptime_seconds,
            "running_containers": m.running_containers,
            "hosting_count": m.hosting_count,
            "recorded_at": m.recorded_at,
        }
        for m in metrics
    ]


@router.get("/vps/{vps_id}/hosting", response_model=List[HostingResponse])
def get_vps_assigned_hosting(vps_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    items = (
        db.query(HostingInstance)
        .filter(HostingInstance.assigned_vps_id == vps_id)
        .order_by(HostingInstance.created_at.desc())
        .all()
    )
    return [build_hosting_response(db, h) for h in items]


@router.get("/vps/{vps_id}/logs")
def get_vps_operation_logs(vps_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    logs = (
        db.query(VpsOperationLog)
        .filter(VpsOperationLog.vps_id == vps_id)
        .order_by(VpsOperationLog.created_at.desc())
        .limit(100)
        .all()
    )
    return [
        {
            "id": lg.id,
            "level": lg.level,
            "message": lg.message,
            "created_at": lg.created_at,
        }
        for lg in logs
    ]


@router.post("/hosting/{hosting_id}/assign-vps", response_model=HostingResponse)
def manual_assign_hosting_vps(
    hosting_id: int,
    payload: AssignVpsRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    hosting = db.query(HostingInstance).filter(HostingInstance.id == hosting_id).first()
    if not hosting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hosting instance not found")

    vps = db.query(VpsServer).filter(VpsServer.id == payload.vps_id).first()
    if not vps:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target VPS not found")

    eligible, reason = is_vps_eligible_for_hosting(
        db=db,
        vps=vps,
        required_cpu=hosting.cpu_limit_cores,
        required_ram_mb=hosting.ram_limit_mb,
        required_storage_mb=hosting.storage_limit_mb,
        hosting_type=hosting.hosting_type,
    )
    if not eligible:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Cannot assign to '{vps.name}': {reason}")

    (
        db.query(VpsHostingAssignment)
        .filter(VpsHostingAssignment.hosting_id == hosting.id, VpsHostingAssignment.is_active.is_(True))
        .update({"is_active": False, "released_at": utcnow()})
    )
    hosting.assigned_vps_id = vps.id
    db.add(
        VpsHostingAssignment(
            hosting_id=hosting.id,
            vps_id=vps.id,
            assigned_by_user_id=admin.id,
            assignment_mode="manual",
            is_active=True,
        )
    )
    _record_vps_op(
        db,
        vps.id,
        admin.id,
        "manual_assign",
        "Completed",
        f"Manually assigned hosting #{hosting.id} ('{hosting.name}') to VPS '{vps.name}'.",
    )
    db.commit()
    db.refresh(hosting)
    return build_hosting_response(db, hosting)


@router.post("/hosting/{hosting_id}/migrate-vps")
def migrate_hosting_between_vps(
    hosting_id: int,
    payload: MigrateVpsRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    hosting = db.query(HostingInstance).filter(HostingInstance.id == hosting_id).first()
    if not hosting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hosting instance not found")

    target_vps = db.query(VpsServer).filter(VpsServer.id == payload.target_vps_id).first()
    if not target_vps:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target VPS not found")

    job = execute_safe_vps_migration(
        db=db,
        hosting=hosting,
        target_vps=target_vps,
        admin_user_id=admin.id,
        preserve_url=payload.preserve_url,
    )
    return {
        "job_id": job.id,
        "status": job.status,
        "hosting": build_hosting_response(db, hosting),
    }


@router.get("/migrations")
def list_vps_migration_jobs(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    jobs = db.query(VpsMigrationJob).order_by(VpsMigrationJob.started_at.desc()).limit(50).all()
    return [
        {
            "id": j.id,
            "hosting_id": j.hosting_id,
            "source_vps_id": j.source_vps_id,
            "target_vps_id": j.target_vps_id,
            "status": j.status,
            "preserve_url": j.preserve_url,
            "error_message": j.error_message,
            "started_at": j.started_at,
            "completed_at": j.completed_at,
        }
        for j in jobs
    ]
