from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import (
    HostingInstance,
    HostingPlan,
    Notification,
    User,
    UserProfile,
    VpsCredentialMetadata,
    VpsServer,
    Wallet,
)
from app.schemas.schemas import HostingResponse, PlanResponse, UserResponse, VpsClusterSummaryResponse, VpsResponse
from app.vps_manager.selector import compute_vps_available_resources


def push_notification(db: Session, user_id: int, title: str, message: str, category: str) -> None:
    db.add(
        Notification(
            user_id=user_id,
            title=title,
            message=message,
            category=category,
            is_read=False,
        )
    )


def build_user_response(db: Session, user: User) -> UserResponse:
    profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).first()
    wallet = db.query(Wallet).filter(Wallet.user_id == user.id).first()
    plan_name = None
    if profile and profile.current_plan_id:
        plan = db.query(HostingPlan).filter(HostingPlan.id == profile.current_plan_id).first()
        if plan:
            plan_name = plan.name
    hosting_count = db.query(func.count(HostingInstance.id)).filter(HostingInstance.user_id == user.id).scalar() or 0
    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=profile.full_name if profile else user.email.split("@")[0],
        phone=profile.phone if profile else None,
        avatar_url=profile.avatar_url if profile else None,
        is_admin=user.is_admin,
        is_active=user.is_active,
        current_plan_name=plan_name,
        plan_expires_at=profile.plan_expires_at if profile else None,
        wallet_balance_bdt=round(wallet.balance_bdt, 2) if wallet else 0.0,
        hosting_count=int(hosting_count),
        created_at=user.created_at,
    )


def build_plan_response(plan: HostingPlan) -> PlanResponse:
    return PlanResponse(
        id=plan.id,
        name=plan.name,
        price_bdt=round(plan.price_bdt, 2),
        duration_days=plan.duration_days,
        cpu_limit_cores=plan.cpu_limit_cores,
        ram_limit_mb=plan.ram_limit_mb,
        storage_limit_mb=plan.storage_limit_mb,
        max_hosting_count=plan.max_hosting_count,
        supports_python=plan.supports_python,
        supports_php=plan.supports_php,
        supports_html=plan.supports_html,
        status=plan.status,
    )


def build_hosting_response(db: Session, h: HostingInstance) -> HostingResponse:
    plan = db.query(HostingPlan).filter(HostingPlan.id == h.plan_id).first() if h.plan_id else None
    vps = db.query(VpsServer).filter(VpsServer.id == h.assigned_vps_id).first() if h.assigned_vps_id else None
    return HostingResponse(
        id=h.id,
        user_id=h.user_id,
        name=h.name,
        hosting_type=h.hosting_type,
        status=h.status,
        plan_name=plan.name if plan else None,
        assigned_vps_id=h.assigned_vps_id,
        assigned_vps_name=vps.name if vps else None,
        assigned_vps_region=vps.region if vps else None,
        cpu_limit_cores=h.cpu_limit_cores,
        ram_limit_mb=h.ram_limit_mb,
        storage_limit_mb=h.storage_limit_mb,
        cpu_usage_percent=round(h.cpu_usage_percent, 1),
        ram_used_mb=h.ram_used_mb,
        storage_used_mb=round(h.storage_used_mb, 2),
        uptime_seconds=h.uptime_seconds,
        public_url=h.public_url,
        php_version=h.php_version,
        entry_file=h.entry_file,
        bot_token_masked=h.bot_token_masked,
        webhook_enabled=h.webhook_enabled,
        created_at=h.created_at,
        expires_at=h.expires_at,
    )


def build_vps_response(db: Session, vps: VpsServer) -> VpsResponse:
    avail_cpu, avail_ram, avail_storage, hosting_count, metric = compute_vps_available_resources(db, vps)
    has_creds = (
        db.query(VpsCredentialMetadata.id).filter(VpsCredentialMetadata.vps_id == vps.id).first() is not None
    )
    return VpsResponse(
        id=vps.id,
        name=vps.name,
        provider=vps.provider,
        ip_address=vps.ip_address,
        ssh_port=vps.ssh_port,
        ssh_username=vps.ssh_username,
        auth_method=vps.auth_method,
        agent_url=vps.agent_url,
        os_name=vps.os_name,
        region=vps.region,
        cpu_capacity_cores=vps.cpu_capacity_cores,
        available_cpu_cores=avail_cpu,
        ram_capacity_mb=vps.ram_capacity_mb,
        available_ram_mb=avail_ram,
        storage_capacity_mb=vps.storage_capacity_mb,
        available_storage_mb=avail_storage,
        reserved_cpu_cores=vps.reserved_cpu_cores,
        reserved_ram_mb=vps.reserved_ram_mb,
        reserved_storage_mb=vps.reserved_storage_mb,
        max_hosting_count=vps.max_hosting_count,
        hosting_count=hosting_count,
        running_containers=metric.running_containers if metric else 0,
        cpu_usage_percent=round(metric.cpu_usage_percent, 1) if metric else 0.0,
        ram_used_mb=metric.ram_used_mb if metric else 0,
        disk_used_mb=metric.disk_used_mb if metric else 0,
        uptime_seconds=metric.uptime_seconds if metric else 0,
        runtime_type=vps.runtime_type,
        connection_status=vps.connection_status,
        last_error=vps.last_error,
        docker_available=vps.docker_available,
        is_enabled=vps.is_enabled,
        maintenance_mode=vps.maintenance_mode,
        has_credentials_configured=has_creds,
        last_health_check_at=vps.last_health_check_at,
    )


def build_vps_cluster_summary(db: Session) -> VpsClusterSummaryResponse:
    servers = db.query(VpsServer).all()
    total_vps = len(servers)
    online_vps = sum(1 for s in servers if s.connection_status == "Online")
    unreachable_vps = sum(1 for s in servers if s.connection_status == "Unreachable")
    offline_vps = sum(1 for s in servers if s.connection_status not in ("Online", "Unreachable"))

    total_cpu = 0.0
    avail_cpu_sum = 0.0
    total_ram = 0
    avail_ram_sum = 0
    total_storage = 0
    avail_storage_sum = 0
    total_containers = 0

    for s in servers:
        total_cpu += s.cpu_capacity_cores
        total_ram += s.ram_capacity_mb
        total_storage += s.storage_capacity_mb
        ac, ar, ast, _, metric = compute_vps_available_resources(db, s)
        if s.connection_status == "Online" and s.is_enabled and not s.maintenance_mode:
            avail_cpu_sum += ac
            avail_ram_sum += ar
            avail_storage_sum += ast
        if metric:
            total_containers += metric.running_containers

    running_hosting = (
        db.query(func.count(HostingInstance.id))
        .filter(HostingInstance.status == "Running")
        .scalar()
        or 0
    )

    return VpsClusterSummaryResponse(
        total_vps=total_vps,
        online_vps=online_vps,
        offline_vps=offline_vps,
        unreachable_vps=unreachable_vps,
        total_cpu_cores=round(total_cpu, 2),
        available_cpu_cores=round(avail_cpu_sum, 2),
        total_ram_mb=total_ram,
        available_ram_mb=avail_ram_sum,
        total_storage_mb=total_storage,
        available_storage_mb=avail_storage_sum,
        total_hosted_containers=total_containers,
        total_running_hosting=int(running_hosting),
    )
