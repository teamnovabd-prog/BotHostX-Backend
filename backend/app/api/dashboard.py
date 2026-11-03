from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.models import (
    Announcement,
    HostingInstance,
    HostingLog,
    HostingPlan,
    Notification,
    SystemSetting,
    User,
    UserProfile,
    WalletTransaction,
)
from app.schemas.schemas import ActivityItem, DashboardResponse
from app.security.auth import get_current_user
from app.services.serializers import build_plan_response, build_user_response

router = APIRouter(tags=["Health & Dashboard"])


@router.get("/health")
@router.get("/api/health")
def health_check(db: Session = Depends(get_db)):
    """
    Production health endpoint checked by the SmartHost / BotHostX Android app and Render.
    Verifies PostgreSQL connectivity and confirms authentication routes are active.
    """
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database connection check failed: {exc}",
        ) from exc

    sys_cfg = db.query(SystemSetting).first()
    return {
        "status": "ok",
        "service": "smarthost-api",
        "platform": sys_cfg.app_name if sys_cfg else "BotHostX",
        "auth_ready": True,
        "database": "connected",
        "maintenance_mode": bool(sys_cfg.maintenance_mode) if sys_cfg else False,
    }


@router.get("/api/dashboard", response_model=DashboardResponse)
def get_dashboard(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    instances = (
        db.query(HostingInstance)
        .filter(HostingInstance.user_id == current_user.id)
        .order_by(HostingInstance.created_at.desc())
        .all()
    )
    total_hosting = len(instances)
    active_hosting = sum(1 for h in instances if h.status == "Running")

    cpu_avg = round(sum(h.cpu_usage_percent for h in instances) / total_hosting, 1) if total_hosting > 0 else 0.0
    ram_used = sum(h.ram_used_mb for h in instances)
    storage_used = round(sum(h.storage_used_mb for h in instances), 2)

    profile = db.query(UserProfile).filter(UserProfile.user_id == current_user.id).first()
    plan = (
        db.query(HostingPlan).filter(HostingPlan.id == profile.current_plan_id).first()
        if profile and profile.current_plan_id
        else None
    )

    ram_limit = plan.ram_limit_mb if plan else sum(h.ram_limit_mb for h in instances)
    storage_limit = plan.storage_limit_mb if plan else sum(h.storage_limit_mb for h in instances)
    nearest_expiry = min((h.expires_at for h in instances), default=profile.plan_expires_at if profile else None)

    if any(h.status == "Error" for h in instances):
        overall_status = "Attention Needed"
    elif active_hosting > 0:
        overall_status = "Operational"
    elif total_hosting > 0:
        overall_status = "Idle"
    else:
        overall_status = "Ready to Deploy"

    unread_count = (
        db.query(func.count(Notification.id))
        .filter(Notification.user_id == current_user.id, Notification.is_read.is_(False))
        .scalar()
        or 0
    )

    activities = []
    recent_txs = (
        db.query(WalletTransaction)
        .filter(WalletTransaction.user_id == current_user.id)
        .order_by(WalletTransaction.created_at.desc())
        .limit(4)
        .all()
    )
    for tx in recent_txs:
        activities.append(
            ActivityItem(
                title=tx.description,
                subtitle=f"৳{tx.amount_bdt:,.2f} • {tx.status}",
                category="wallet",
                timestamp=tx.created_at,
            )
        )

    if instances:
        host_ids = [h.id for h in instances]
        recent_logs = (
            db.query(HostingLog)
            .filter(HostingLog.hosting_id.in_(host_ids), HostingLog.log_type == "system")
            .order_by(HostingLog.created_at.desc())
            .limit(5)
            .all()
        )
        for lg in recent_logs:
            activities.append(
                ActivityItem(
                    title=lg.message[:80],
                    subtitle=f"Hosting #{lg.hosting_id}",
                    category="hosting",
                    timestamp=lg.created_at,
                )
            )
    activities.sort(key=lambda a: a.timestamp, reverse=True)

    published_announcements = (
        db.query(Announcement)
        .filter(Announcement.is_published.is_(True))
        .order_by(Announcement.created_at.desc())
        .limit(3)
        .all()
    )

    return DashboardResponse(
        user=build_user_response(db, current_user),
        active_hosting_count=active_hosting,
        total_hosting_count=total_hosting,
        current_plan=build_plan_response(plan) if plan else None,
        overall_status=overall_status,
        cpu_usage_percent=cpu_avg,
        ram_used_mb=ram_used,
        ram_limit_mb=ram_limit,
        storage_used_mb=storage_used,
        storage_limit_mb=storage_limit,
        nearest_expiry=nearest_expiry,
        unread_notifications=int(unread_count),
        recent_activities=activities[:6],
        announcements=[
            {"id": str(a.id), "title": a.title, "content": a.content}
            for a in published_announcements
        ],
    )
