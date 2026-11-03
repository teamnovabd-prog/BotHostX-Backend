from typing import Tuple
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import HostingInstance, VpsResourceMetric, VpsServer


def compute_vps_available_resources(db: Session, vps: VpsServer) -> Tuple[float, int, int, int, VpsResourceMetric | None]:
    """Calculates available CPU, RAM, Storage, and active hosting count using allocated & actual telemetry."""
    allocated = (
        db.query(
            func.coalesce(func.sum(HostingInstance.cpu_limit_cores), 0.0),
            func.coalesce(func.sum(HostingInstance.ram_limit_mb), 0),
            func.coalesce(func.sum(HostingInstance.storage_limit_mb), 0),
            func.count(HostingInstance.id),
        )
        .filter(HostingInstance.assigned_vps_id == vps.id)
        .first()
    )
    alloc_cpu = float(allocated[0] if allocated else 0.0)
    alloc_ram = int(allocated[1] if allocated else 0)
    alloc_storage = int(allocated[2] if allocated else 0)
    hosting_count = int(allocated[3] if allocated else 0)

    latest_metric = (
        db.query(VpsResourceMetric)
        .filter(VpsResourceMetric.vps_id == vps.id)
        .order_by(VpsResourceMetric.recorded_at.desc())
        .first()
    )

    actual_ram_used = latest_metric.ram_used_mb if latest_metric else 0
    actual_disk_used = latest_metric.disk_used_mb if latest_metric else 0

    usable_cpu = max(0.0, round(vps.cpu_capacity_cores - vps.reserved_cpu_cores - alloc_cpu, 2))
    usable_ram = max(
        0,
        min(
            vps.ram_capacity_mb - vps.reserved_ram_mb - alloc_ram,
            vps.ram_capacity_mb - actual_ram_used,
        ),
    )
    usable_storage = max(
        0,
        min(
            vps.storage_capacity_mb - vps.reserved_storage_mb - alloc_storage,
            vps.storage_capacity_mb - actual_disk_used,
        ),
    )
    return usable_cpu, usable_ram, usable_storage, hosting_count, latest_metric


def is_vps_eligible_for_hosting(
    db: Session,
    vps: VpsServer,
    required_cpu: float,
    required_ram_mb: int,
    required_storage_mb: int,
    hosting_type: str,
) -> Tuple[bool, str]:
    if not vps.is_enabled:
        return False, "VPS is disabled"
    if vps.maintenance_mode:
        return False, "VPS is in maintenance mode"
    if vps.connection_status != "Online" or not vps.docker_available:
        return False, f"VPS is not Online with verified Docker runtime (current: {vps.connection_status})"
    if "docker" not in vps.runtime_type.lower():
        return False, f"VPS runtime '{vps.runtime_type}' is incompatible with isolated {hosting_type} containers"

    avail_cpu, avail_ram, avail_storage, count, _ = compute_vps_available_resources(db, vps)
    if count >= vps.max_hosting_count:
        return False, "VPS has reached its maximum hosting instance count"
    if avail_cpu < required_cpu:
        return False, f"Insufficient CPU capacity (available {avail_cpu} cores, needed {required_cpu})"
    if avail_ram < required_ram_mb:
        return False, f"Insufficient RAM capacity (available {avail_ram} MB, needed {required_ram_mb} MB)"
    if avail_storage < required_storage_mb:
        return False, f"Insufficient storage capacity (available {avail_storage} MB, needed {required_storage_mb} MB)"
    return True, "Eligible"


def select_optimal_vps(
    db: Session,
    required_cpu: float,
    required_ram_mb: int,
    required_storage_mb: int,
    hosting_type: str,
    policy: str = "most_available_resources",
) -> VpsServer | None:
    """Selects an Online, verified VPS with sufficient capacity using the configured policy."""
    candidates = (
        db.query(VpsServer)
        .filter(
            VpsServer.is_enabled.is_(True),
            VpsServer.maintenance_mode.is_(False),
            VpsServer.connection_status == "Online",
            VpsServer.docker_available.is_(True),
        )
        .all()
    )

    scored = []
    for vps in candidates:
        eligible, _ = is_vps_eligible_for_hosting(
            db, vps, required_cpu, required_ram_mb, required_storage_mb, hosting_type
        )
        if not eligible:
            continue
        avail_cpu, avail_ram, avail_storage, count, _ = compute_vps_available_resources(db, vps)
        if policy == "least_instances":
            score = -count
        else:
            score = (avail_ram / 1024.0) + (avail_cpu * 2.0) + (avail_storage / 10240.0)
        scored.append((score, vps))

    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]
