import secrets
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.support_notifications import _build_ticket_response
from app.database.session import get_db
from app.models.models import (
    Announcement,
    DepositRequest,
    HostingInstance,
    HostingPlan,
    Payment,
    SupportTicket,
    SystemSetting,
    User,
    Wallet,
    WalletTransaction,
    utcnow,
)
from app.schemas.schemas import (
    AdminDashboardResponse,
    AnnouncementWriteRequest,
    DepositResponse,
    DepositReviewRequest,
    HostingResponse,
    PlanResponse,
    PlanWriteRequest,
    SupportTicketResponse,
    SystemSettingsUpdateRequest,
    UserResponse,
)
from app.security.auth import require_admin
from app.services.serializers import (
    build_hosting_response,
    build_plan_response,
    build_user_response,
    build_vps_cluster_summary,
    push_notification,
)

router = APIRouter(prefix="/api/admin", tags=["Admin Panel"])


@router.get("/dashboard", response_model=AdminDashboardResponse)
def get_admin_dashboard(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    total_users = db.query(func.count(User.id)).filter(User.is_admin.is_(False)).scalar() or 0
    active_users = (
        db.query(func.count(User.id))
        .filter(User.is_admin.is_(False), User.is_active.is_(True))
        .scalar()
        or 0
    )
    total_hosting = db.query(func.count(HostingInstance.id)).scalar() or 0
    running_hosting = (
        db.query(func.count(HostingInstance.id))
        .filter(HostingInstance.status == "Running")
        .scalar()
        or 0
    )
    stopped_hosting = (
        db.query(func.count(HostingInstance.id))
        .filter(HostingInstance.status != "Running")
        .scalar()
        or 0
    )
    pending_deposits = (
        db.query(func.count(DepositRequest.id))
        .filter(DepositRequest.status == "Pending")
        .scalar()
        or 0
    )
    total_revenue = (
        db.query(func.coalesce(func.sum(DepositRequest.amount_bdt), 0.0))
        .filter(DepositRequest.status == "Approved")
        .scalar()
        or 0.0
    )

    return AdminDashboardResponse(
        total_users=int(total_users),
        active_users=int(active_users),
        total_hosting=int(total_hosting),
        running_hosting=int(running_hosting),
        stopped_hosting=int(stopped_hosting),
        pending_deposits=int(pending_deposits),
        total_revenue_bdt=round(float(total_revenue), 2),
        vps_summary=build_vps_cluster_summary(db),
    )


# --- User Management ---
@router.get("/users", response_model=List[UserResponse])
def admin_list_users(
    search: Optional[str] = Query(default=None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = db.query(User).filter(User.is_admin.is_(False))
    if search and search.strip():
        q = q.filter(User.email.ilike(f"%{search.strip()}%"))
    users = q.order_by(User.created_at.desc()).all()
    return [build_user_response(db, u) for u in users]


@router.get("/users/{user_id}", response_model=UserResponse)
def admin_get_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return build_user_response(db, user)


@router.post("/users/{user_id}/suspend", response_model=UserResponse)
def admin_suspend_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id, User.is_admin.is_(False)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target user not found")
    user.is_active = False
    db.commit()
    return build_user_response(db, user)


@router.post("/users/{user_id}/activate", response_model=UserResponse)
def admin_activate_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id, User.is_admin.is_(False)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target user not found")
    user.is_active = True
    db.commit()
    return build_user_response(db, user)


@router.delete("/users/{user_id}")
def admin_delete_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id, User.is_admin.is_(False)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target user not found")
    running_cnt = (
        db.query(func.count(HostingInstance.id))
        .filter(HostingInstance.user_id == user.id, HostingInstance.status == "Running")
        .scalar()
        or 0
    )
    if running_cnt > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Stop or remove active running hosting instances before deleting this user.",
        )
    db.delete(user)
    db.commit()
    return {"message": "User deleted"}


# --- Hosting Management ---
@router.get("/hosting", response_model=List[HostingResponse])
def admin_list_all_hosting(
    search: Optional[str] = Query(default=None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = db.query(HostingInstance)
    if search and search.strip():
        q = q.filter(HostingInstance.name.ilike(f"%{search.strip()}%"))
    items = q.order_by(HostingInstance.created_at.desc()).all()
    return [build_hosting_response(db, h) for h in items]


# --- Plan Management ---
@router.get("/plans", response_model=List[PlanResponse])
def admin_list_plans(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    plans = db.query(HostingPlan).order_by(HostingPlan.price_bdt.asc()).all()
    return [build_plan_response(p) for p in plans]


@router.post("/plans", response_model=PlanResponse, status_code=status.HTTP_201_CREATED)
def admin_create_plan(
    payload: PlanWriteRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if db.query(HostingPlan).filter(HostingPlan.name == payload.name.strip()).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Plan with this name already exists")
    plan = HostingPlan(
        name=payload.name.strip(),
        price_bdt=payload.price_bdt,
        duration_days=payload.duration_days,
        cpu_limit_cores=payload.cpu_limit_cores,
        ram_limit_mb=payload.ram_limit_mb,
        storage_limit_mb=payload.storage_limit_mb,
        max_hosting_count=payload.max_hosting_count,
        supports_python=payload.supports_python,
        supports_php=payload.supports_php,
        supports_html=payload.supports_html,
        status=payload.status,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return build_plan_response(plan)


@router.put("/plans/{plan_id}", response_model=PlanResponse)
def admin_update_plan(
    plan_id: int,
    payload: PlanWriteRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    plan = db.query(HostingPlan).filter(HostingPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    plan.name = payload.name.strip()
    plan.price_bdt = payload.price_bdt
    plan.duration_days = payload.duration_days
    plan.cpu_limit_cores = payload.cpu_limit_cores
    plan.ram_limit_mb = payload.ram_limit_mb
    plan.storage_limit_mb = payload.storage_limit_mb
    plan.max_hosting_count = payload.max_hosting_count
    plan.supports_python = payload.supports_python
    plan.supports_php = payload.supports_php
    plan.supports_html = payload.supports_html
    plan.status = payload.status
    db.commit()
    db.refresh(plan)
    return build_plan_response(plan)


@router.delete("/plans/{plan_id}")
def admin_delete_plan(plan_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    plan = db.query(HostingPlan).filter(HostingPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    db.delete(plan)
    db.commit()
    return {"message": "Plan deleted"}


# --- Deposit Management ---
@router.get("/deposits", response_model=List[DepositResponse])
def admin_list_deposits(
    status_filter: Optional[str] = Query(default=None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = db.query(DepositRequest)
    if status_filter:
        q = q.filter(DepositRequest.status == status_filter)
    deps = q.order_by(DepositRequest.created_at.desc()).all()
    results = []
    for d in deps:
        u = db.query(User).filter(User.id == d.user_id).first()
        results.append(
            DepositResponse(
                id=d.id,
                user_id=d.user_id,
                user_email=u.email if u else None,
                payment_method=d.payment_method,
                amount_bdt=round(d.amount_bdt, 2),
                transaction_reference=d.transaction_reference,
                sender_number=d.sender_number,
                status=d.status,
                admin_note=d.admin_note,
                created_at=d.created_at,
                reviewed_at=d.reviewed_at,
            )
        )
    return results


@router.post("/deposits/{deposit_id}/review", response_model=DepositResponse)
def admin_review_deposit(
    deposit_id: int,
    payload: DepositReviewRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    dep = db.query(DepositRequest).filter(DepositRequest.id == deposit_id).with_for_update().first()
    if not dep:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deposit request not found")
    if dep.status != "Pending":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Deposit request has already been reviewed")

    dep.reviewed_by_id = admin.id
    dep.reviewed_at = utcnow()
    dep.admin_note = payload.admin_note

    if payload.approve:
        dep.status = "Approved"
        wallet = db.query(Wallet).filter(Wallet.user_id == dep.user_id).with_for_update().first()
        if wallet is None:
            wallet = Wallet(user_id=dep.user_id, balance_bdt=0.0, currency="BDT")
            db.add(wallet)
            db.flush()
        wallet.balance_bdt = round(wallet.balance_bdt + dep.amount_bdt, 2)
        tx_ref = f"DEP-{dep.id}-{secrets.token_hex(3).upper()}"
        db.add(
            WalletTransaction(
                wallet_id=wallet.id,
                user_id=dep.user_id,
                tx_type="deposit",
                amount_bdt=dep.amount_bdt,
                status="Completed",
                reference_id=tx_ref,
                description=f"{dep.payment_method} Deposit Approved (Ref: {dep.transaction_reference})",
            )
        )
        db.add(
            Payment(
                user_id=dep.user_id,
                deposit_request_id=dep.id,
                gateway=dep.payment_method,
                transaction_id=dep.transaction_reference,
                amount_bdt=dep.amount_bdt,
                currency="BDT",
                status="Approved",
            )
        )
        push_notification(
            db,
            dep.user_id,
            "Deposit Approved",
            f"Your {dep.payment_method} deposit of ৳{dep.amount_bdt:,.2f} has been credited to your wallet.",
            "Deposit Approved",
        )
    else:
        dep.status = "Rejected"
        push_notification(
            db,
            dep.user_id,
            "Deposit Rejected",
            f"Your {dep.payment_method} deposit of ৳{dep.amount_bdt:,.2f} was rejected. {payload.admin_note or ''}".strip(),
            "Deposit Rejected",
        )

    db.commit()
    db.refresh(dep)
    u = db.query(User).filter(User.id == dep.user_id).first()
    return DepositResponse(
        id=dep.id,
        user_id=dep.user_id,
        user_email=u.email if u else None,
        payment_method=dep.payment_method,
        amount_bdt=dep.amount_bdt,
        transaction_reference=dep.transaction_reference,
        sender_number=dep.sender_number,
        status=dep.status,
        admin_note=dep.admin_note,
        created_at=dep.created_at,
        reviewed_at=dep.reviewed_at,
    )


# --- Support Management ---
@router.get("/support/tickets", response_model=List[SupportTicketResponse])
def admin_list_tickets(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    tickets = db.query(SupportTicket).order_by(SupportTicket.updated_at.desc()).all()
    return [_build_ticket_response(db, t) for t in tickets]


@router.post("/support/tickets/{ticket_id}/status", response_model=SupportTicketResponse)
def admin_update_ticket_status(
    ticket_id: int,
    new_status: str = Query(..., pattern="^(Open|In Progress|Replied|Closed)$"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    ticket = db.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    ticket.status = new_status
    db.commit()
    db.refresh(ticket)
    return _build_ticket_response(db, ticket)


# --- Announcement Management ---
@router.get("/announcements")
def admin_list_announcements(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    items = db.query(Announcement).order_by(Announcement.created_at.desc()).all()
    return [
        {
            "id": a.id,
            "title": a.title,
            "content": a.content,
            "is_published": a.is_published,
            "created_at": a.created_at,
        }
        for a in items
    ]


@router.post("/announcements", status_code=status.HTTP_201_CREATED)
def admin_create_announcement(
    payload: AnnouncementWriteRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    ann = Announcement(
        title=payload.title.strip(),
        content=payload.content.strip(),
        is_published=payload.is_published,
        created_by_id=admin.id,
        published_at=utcnow() if payload.is_published else None,
    )
    db.add(ann)
    if payload.is_published:
        for u in db.query(User).filter(User.is_active.is_(True)).all():
            push_notification(db, u.id, f"Announcement: {ann.title}", ann.content[:200], "New Announcement")
    db.commit()
    db.refresh(ann)
    return {"id": ann.id, "title": ann.title, "content": ann.content, "is_published": ann.is_published}


@router.put("/announcements/{announcement_id}")
def admin_update_announcement(
    announcement_id: int,
    payload: AnnouncementWriteRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    ann = db.query(Announcement).filter(Announcement.id == announcement_id).first()
    if not ann:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Announcement not found")
    ann.title = payload.title.strip()
    ann.content = payload.content.strip()
    ann.is_published = payload.is_published
    db.commit()
    return {"id": ann.id, "title": ann.title, "content": ann.content, "is_published": ann.is_published}


@router.delete("/announcements/{announcement_id}")
def admin_delete_announcement(announcement_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    ann = db.query(Announcement).filter(Announcement.id == announcement_id).first()
    if not ann:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Announcement not found")
    db.delete(ann)
    db.commit()
    return {"message": "Announcement deleted"}


# --- System & Payment Settings ---
@router.get("/settings")
def admin_get_settings(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    cfg = db.query(SystemSetting).first()
    return {
        "app_name": cfg.app_name,
        "hosting_domain": cfg.hosting_domain,
        "api_base_url": cfg.api_base_url,
        "maintenance_mode": cfg.maintenance_mode,
        "max_upload_size_mb": cfg.max_upload_size_mb,
        "allowed_file_types": cfg.allowed_file_types,
        "default_vps_selection_policy": cfg.default_vps_selection_policy,
        "hosting_assignment_policy": cfg.hosting_assignment_policy,
        "bkash_number": cfg.bkash_number,
        "bkash_enabled": cfg.bkash_enabled,
        "nagad_number": cfg.nagad_number,
        "nagad_enabled": cfg.nagad_enabled,
        "rocket_number": cfg.rocket_number,
        "rocket_enabled": cfg.rocket_enabled,
        "manual_payment_instructions": cfg.manual_payment_instructions,
        "manual_enabled": cfg.manual_enabled,
    }


@router.put("/settings")
def admin_update_settings(
    payload: SystemSettingsUpdateRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    cfg = db.query(SystemSetting).first()
    if cfg is None:
        cfg = SystemSetting(id=1)
        db.add(cfg)

    was_maint = cfg.maintenance_mode
    cfg.app_name = payload.app_name.strip()
    cfg.hosting_domain = payload.hosting_domain.strip()
    cfg.api_base_url = payload.api_base_url.strip()
    cfg.maintenance_mode = payload.maintenance_mode
    cfg.max_upload_size_mb = payload.max_upload_size_mb
    cfg.allowed_file_types = payload.allowed_file_types.strip()
    cfg.default_vps_selection_policy = payload.default_vps_selection_policy.strip()
    cfg.hosting_assignment_policy = payload.hosting_assignment_policy.strip()
    cfg.bkash_number = payload.bkash_number.strip()
    cfg.bkash_enabled = payload.bkash_enabled
    cfg.nagad_number = payload.nagad_number.strip()
    cfg.nagad_enabled = payload.nagad_enabled
    cfg.rocket_number = payload.rocket_number.strip()
    cfg.rocket_enabled = payload.rocket_enabled
    cfg.manual_payment_instructions = payload.manual_payment_instructions.strip()
    cfg.manual_enabled = payload.manual_enabled

    if payload.maintenance_mode and not was_maint:
        for u in db.query(User).filter(User.is_active.is_(True)).all():
            push_notification(
                db,
                u.id,
                "Maintenance Notice",
                "Scheduled system maintenance is now active.",
                "Maintenance Notice",
            )
    db.commit()
    return admin_get_settings(admin, db)
