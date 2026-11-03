from datetime import timedelta
import secrets
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.models import (
    DepositRequest,
    HostingInstance,
    HostingPlan,
    SystemSetting,
    User,
    UserProfile,
    Wallet,
    WalletTransaction,
    utcnow,
)
from app.schemas.schemas import (
    DepositCreateRequest,
    DepositResponse,
    PaymentMethodInfo,
    PlanResponse,
    PurchasePlanRequest,
    WalletOverviewResponse,
    WalletTransactionResponse,
)
from app.security.auth import get_current_user
from app.services.serializers import build_plan_response, push_notification

router = APIRouter(prefix="/api", tags=["Plans & Wallet"])


@router.get("/plans", response_model=List[PlanResponse])
def list_active_plans(db: Session = Depends(get_db)):
    plans = (
        db.query(HostingPlan)
        .filter(HostingPlan.status == "Active")
        .order_by(HostingPlan.price_bdt.asc())
        .all()
    )
    return [build_plan_response(p) for p in plans]


@router.get("/plans/current", response_model=Optional[PlanResponse])
def get_current_plan(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = db.query(UserProfile).filter(UserProfile.user_id == current_user.id).first()
    if not profile or not profile.current_plan_id:
        return None
    plan = db.query(HostingPlan).filter(HostingPlan.id == profile.current_plan_id).first()
    return build_plan_response(plan) if plan else None


@router.post("/plans/purchase", response_model=PlanResponse)
def purchase_or_upgrade_plan(
    payload: PurchasePlanRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    plan = db.query(HostingPlan).filter(HostingPlan.id == payload.plan_id, HostingPlan.status == "Active").first()
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Selected hosting plan is unavailable")

    wallet = db.query(Wallet).filter(Wallet.user_id == current_user.id).with_for_update().first()
    if wallet is None:
        wallet = Wallet(user_id=current_user.id, balance_bdt=0.0, currency="BDT")
        db.add(wallet)
        db.flush()

    if wallet.balance_bdt < plan.price_bdt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient wallet balance (৳{wallet.balance_bdt:,.2f}). Plan costs ৳{plan.price_bdt:,.2f}. Please deposit funds first.",
        )

    if plan.price_bdt > 0:
        wallet.balance_bdt = round(wallet.balance_bdt - plan.price_bdt, 2)
        tx_ref = f"PLN-{secrets.token_hex(5).upper()}"
        db.add(
            WalletTransaction(
                wallet_id=wallet.id,
                user_id=current_user.id,
                tx_type="plan_purchase",
                amount_bdt=plan.price_bdt,
                status="Completed",
                reference_id=tx_ref,
                description=f"Purchased {plan.name} ({plan.duration_days} days)",
            )
        )

    profile = db.query(UserProfile).filter(UserProfile.user_id == current_user.id).first()
    if profile is None:
        profile = UserProfile(user_id=current_user.id, full_name=current_user.email.split("@")[0])
        db.add(profile)

    new_expiry = utcnow() + timedelta(days=plan.duration_days)
    profile.current_plan_id = plan.id
    profile.plan_expires_at = new_expiry

    # Update resource limits & expiry on user's existing hosting instances
    instances = db.query(HostingInstance).filter(HostingInstance.user_id == current_user.id).all()
    for inst in instances:
        inst.plan_id = plan.id
        inst.cpu_limit_cores = plan.cpu_limit_cores
        inst.ram_limit_mb = plan.ram_limit_mb
        inst.storage_limit_mb = plan.storage_limit_mb
        inst.expires_at = new_expiry
        if inst.status == "Expired":
            inst.status = "Stopped"

    push_notification(
        db,
        current_user.id,
        "Plan Purchased",
        f"Your account is now on {plan.name} until {new_expiry.strftime('%Y-%m-%d')}.",
        "Plan Purchased",
    )
    db.commit()
    return build_plan_response(plan)


@router.get("/wallet", response_model=WalletOverviewResponse)
def get_wallet_overview(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wallet = db.query(Wallet).filter(Wallet.user_id == current_user.id).first()
    sys_cfg = db.query(SystemSetting).first()

    methods = []
    if sys_cfg:
        methods = [
            PaymentMethodInfo(
                method="bKash",
                enabled=sys_cfg.bkash_enabled,
                account_number=sys_cfg.bkash_number,
                instructions="Send Money / Payment in BDT (৳) to bKash number and enter your TrxID.",
            ),
            PaymentMethodInfo(
                method="Nagad",
                enabled=sys_cfg.nagad_enabled,
                account_number=sys_cfg.nagad_number,
                instructions="Send Money in BDT (৳) to Nagad number and submit the TxnID.",
            ),
            PaymentMethodInfo(
                method="Rocket",
                enabled=sys_cfg.rocket_enabled,
                account_number=sys_cfg.rocket_number,
                instructions="Transfer BDT (৳) to Rocket account and provide your Transaction Reference.",
            ),
            PaymentMethodInfo(
                method="Manual",
                enabled=sys_cfg.manual_enabled,
                account_number="See Instructions",
                instructions=sys_cfg.manual_payment_instructions,
            ),
        ]

    return WalletOverviewResponse(
        balance_bdt=round(wallet.balance_bdt, 2) if wallet else 0.0,
        currency="BDT",
        payment_methods=[m for m in methods if m.enabled],
    )


@router.get("/wallet/transactions", response_model=List[WalletTransactionResponse])
def list_wallet_transactions(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    txs = (
        db.query(WalletTransaction)
        .filter(WalletTransaction.user_id == current_user.id)
        .order_by(WalletTransaction.created_at.desc())
        .limit(100)
        .all()
    )
    return [
        WalletTransactionResponse(
            id=t.id,
            tx_type=t.tx_type,
            amount_bdt=round(t.amount_bdt, 2),
            status=t.status,
            reference_id=t.reference_id,
            description=t.description,
            created_at=t.created_at,
        )
        for t in txs
    ]


@router.get("/wallet/deposits", response_model=List[DepositResponse])
def list_user_deposits(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    deps = (
        db.query(DepositRequest)
        .filter(DepositRequest.user_id == current_user.id)
        .order_by(DepositRequest.created_at.desc())
        .limit(100)
        .all()
    )
    return [
        DepositResponse(
            id=d.id,
            user_id=d.user_id,
            user_email=current_user.email,
            payment_method=d.payment_method,
            amount_bdt=round(d.amount_bdt, 2),
            transaction_reference=d.transaction_reference,
            sender_number=d.sender_number,
            status=d.status,
            admin_note=d.admin_note,
            created_at=d.created_at,
            reviewed_at=d.reviewed_at,
        )
        for d in deps
    ]


@router.post("/wallet/deposit", response_model=DepositResponse, status_code=status.HTTP_201_CREATED)
def submit_manual_deposit(
    payload: DepositCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ref_clean = payload.transaction_reference.strip()
    dup = db.query(DepositRequest).filter(DepositRequest.transaction_reference == ref_clean).first()
    if dup is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This transaction reference has already been submitted.",
        )

    sys_cfg = db.query(SystemSetting).first()
    if sys_cfg:
        enabled_map = {
            "bKash": sys_cfg.bkash_enabled,
            "Nagad": sys_cfg.nagad_enabled,
            "Rocket": sys_cfg.rocket_enabled,
            "Manual": sys_cfg.manual_enabled,
        }
        if not enabled_map.get(payload.payment_method, False):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{payload.payment_method} deposits are currently disabled by Admin.",
            )

    dep = DepositRequest(
        user_id=current_user.id,
        payment_method=payload.payment_method,
        amount_bdt=round(payload.amount_bdt, 2),
        transaction_reference=ref_clean,
        sender_number=payload.sender_number.strip() if payload.sender_number else None,
        status="Pending",
    )
    db.add(dep)
    db.commit()
    db.refresh(dep)

    return DepositResponse(
        id=dep.id,
        user_id=dep.user_id,
        user_email=current_user.email,
        payment_method=dep.payment_method,
        amount_bdt=dep.amount_bdt,
        transaction_reference=dep.transaction_reference,
        sender_number=dep.sender_number,
        status=dep.status,
        admin_note=dep.admin_note,
        created_at=dep.created_at,
        reviewed_at=dep.reviewed_at,
    )
