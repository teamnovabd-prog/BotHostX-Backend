from datetime import timedelta
import json
import re
import secrets
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.hosting_runtime.isolation import build_isolated_container_spec, get_isolated_staging_dir
from app.models.models import (
    HostingInstance,
    HostingLog,
    HostingPlan,
    SystemSetting,
    User,
    UserProfile,
    VpsHostingAssignment,
    VpsServer,
    utcnow,
)
from app.schemas.schemas import (
    CreateHostingRequest,
    HostingResponse,
    LogEntryResponse,
    UpdateBotConfigRequest,
)
from app.security.auth import get_current_user
from app.security.crypto import decrypt_secret, encrypt_secret, mask_secret
from app.services.serializers import build_hosting_response, push_notification
from app.vps_manager.client import VpsManagementClient
from app.vps_manager.selector import select_optimal_vps

router = APIRouter(prefix="/api/hosting", tags=["Hosting"])


def _get_owned_hosting(db: Session, hosting_id: int, user: User) -> HostingInstance:
    hosting = db.query(HostingInstance).filter(HostingInstance.id == hosting_id).first()
    if hosting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hosting instance not found")
    if hosting.user_id != user.id and not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this hosting instance",
        )
    return hosting


def _record_log(db: Session, hosting_id: int, log_type: str, message: str) -> None:
    db.add(HostingLog(hosting_id=hosting_id, log_type=log_type, message=message))


def _build_runtime_spec(hosting: HostingInstance) -> dict:
    env_vars = {}
    if hosting.encrypted_env_json:
        env_vars = json.loads(decrypt_secret(hosting.encrypted_env_json))
    if hosting.encrypted_bot_token:
        env_vars["BOT_TOKEN"] = decrypt_secret(hosting.encrypted_bot_token)
    return build_isolated_container_spec(
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


@router.get("", response_model=List[HostingResponse])
def list_user_hosting(
    hosting_type: Optional[str] = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(HostingInstance).filter(HostingInstance.user_id == current_user.id)
    if hosting_type:
        q = q.filter(HostingInstance.hosting_type == hosting_type)
    items = q.order_by(HostingInstance.created_at.desc()).all()
    return [build_hosting_response(db, h) for h in items]


@router.post("", response_model=HostingResponse, status_code=status.HTTP_201_CREATED)
def create_hosting(
    payload: CreateHostingRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sys_cfg = db.query(SystemSetting).first()
    if sys_cfg and sys_cfg.maintenance_mode and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Platform is currently in maintenance mode. Please try again shortly.",
        )

    profile = db.query(UserProfile).filter(UserProfile.user_id == current_user.id).first()
    if not profile or not profile.current_plan_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An active hosting plan is required before creating a hosting instance.",
        )

    plan = db.query(HostingPlan).filter(HostingPlan.id == profile.current_plan_id).first()
    if not plan or plan.status != "Active":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected plan is no longer active.")
    if profile.plan_expires_at and profile.plan_expires_at < utcnow():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Your hosting plan has expired. Please renew or upgrade.")

    # Enforce hosting type eligibility
    if payload.hosting_type in ("python_bot", "telegram_bot") and not plan.supports_python:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your current plan does not include Python/Telegram Bot hosting.")
    if payload.hosting_type == "php_script" and not plan.supports_php:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your current plan does not include PHP hosting.")
    if payload.hosting_type == "html_web" and not plan.supports_html:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your current plan does not include HTML website hosting.")

    existing_count = (
        db.query(func.count(HostingInstance.id))
        .filter(HostingInstance.user_id == current_user.id)
        .scalar()
        or 0
    )
    if existing_count >= plan.max_hosting_count:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Plan limit reached ({plan.max_hosting_count} hosting instances max). Upgrade your plan to create more.",
        )

    # Select an Online, verified VPS if automatic assignment is enabled
    policy = sys_cfg.default_vps_selection_policy if sys_cfg else "most_available_resources"
    assignment_policy = sys_cfg.hosting_assignment_policy if sys_cfg else "auto"
    selected_vps = None
    if assignment_policy == "auto":
        selected_vps = select_optimal_vps(
            db=db,
            required_cpu=plan.cpu_limit_cores,
            required_ram_mb=plan.ram_limit_mb,
            required_storage_mb=plan.storage_limit_mb,
            hosting_type=payload.hosting_type,
            policy=policy,
        )
        if selected_vps is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No Online VPS node with sufficient verified Docker capacity is currently available. Ask Admin to connect/verify a VPS server.",
            )

    slug = None
    public_url = None
    php_ver = "8.3" if payload.hosting_type == "php_script" else None
    if payload.hosting_type in ("php_script", "html_web"):
        raw_slug = payload.subdomain_slug or re.sub(r"[^a-z0-9-]", "-", payload.name.lower()).strip("-")
        slug = f"{raw_slug[:32]}-{secrets.token_hex(2)}"
        configured_domain = (sys_cfg.hosting_domain.strip() if sys_cfg and sys_cfg.hosting_domain else "")
        if configured_domain and configured_domain != "example.com":
            public_url = f"https://{slug}.{configured_domain}"

    enc_token = encrypt_secret(payload.bot_token.strip()) if payload.bot_token else None
    masked_token = mask_secret(payload.bot_token.strip()) if payload.bot_token else None
    enc_env = encrypt_secret(json.dumps(payload.env_vars)) if payload.env_vars else None
    webhook_secret = secrets.token_urlsafe(32) if payload.webhook_enabled else None

    default_entry = payload.entry_file.strip() if payload.entry_file else (
        "index.php" if payload.hosting_type == "php_script" else
        "index.html" if payload.hosting_type == "html_web" else "main.py"
    )

    expires_at = profile.plan_expires_at or (utcnow() + timedelta(days=plan.duration_days))

    hosting = HostingInstance(
        user_id=current_user.id,
        plan_id=plan.id,
        assigned_vps_id=selected_vps.id if selected_vps else None,
        name=payload.name.strip(),
        hosting_type=payload.hosting_type,
        status="Stopped",
        cpu_limit_cores=plan.cpu_limit_cores,
        ram_limit_mb=plan.ram_limit_mb,
        storage_limit_mb=plan.storage_limit_mb,
        subdomain_slug=slug,
        public_url=public_url,
        php_version=php_ver,
        entry_file=default_entry,
        encrypted_bot_token=enc_token,
        bot_token_masked=masked_token,
        encrypted_env_json=enc_env,
        webhook_enabled=payload.webhook_enabled,
        webhook_secret_path=webhook_secret,
        expires_at=expires_at,
    )
    db.add(hosting)
    db.flush()

    # Initialize isolated directory
    get_isolated_staging_dir(current_user.id, hosting.id)

    if selected_vps:
        db.add(
            VpsHostingAssignment(
                hosting_id=hosting.id,
                vps_id=selected_vps.id,
                assigned_by_user_id=current_user.id,
                assignment_mode="auto",
                is_active=True,
            )
        )

    _record_log(
        db,
        hosting.id,
        "system",
        f"Hosting '{hosting.name}' ({hosting.hosting_type}) provisioned"
        + (f" on VPS '{selected_vps.name}'." if selected_vps else " awaiting Admin VPS assignment."),
    )
    push_notification(
        db,
        current_user.id,
        "Hosting Created",
        f"'{hosting.name}' has been provisioned. Upload your project files and press Start.",
        "Hosting Created",
    )
    db.commit()
    db.refresh(hosting)
    return build_hosting_response(db, hosting)


@router.get("/{hosting_id}", response_model=HostingResponse)
def get_hosting_detail(
    hosting_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    hosting = _get_owned_hosting(db, hosting_id, current_user)
    return build_hosting_response(db, hosting)


def _control_hosting_lifecycle(db: Session, hosting: HostingInstance, user: User, action: str) -> HostingResponse:
    if hosting.expires_at < utcnow():
        hosting.status = "Expired"
        db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Hosting has expired. Please renew your plan.")

    if hosting.assigned_vps_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No VPS server is assigned to this hosting instance yet.",
        )

    vps = db.query(VpsServer).filter(VpsServer.id == hosting.assigned_vps_id).first()
    if vps is None or vps.connection_status != "Online" or not vps.docker_available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Assigned VPS is not currently Online (status: {vps.connection_status if vps else 'Missing'}).",
        )

    transitional = {"start": "Starting", "restart": "Restarting", "stop": "Stopped", "install_deps": hosting.status}
    hosting.status = transitional.get(action, hosting.status)
    db.commit()

    client = VpsManagementClient(db)
    spec = _build_runtime_spec(hosting)
    staging_dir = get_isolated_staging_dir(hosting.user_id, hosting.id)

    try:
        res = client.sync_workspace_and_control_container(vps, action, spec, staging_dir)
        if action in ("start", "restart"):
            hosting.status = "Running"
            hosting.container_id = str(res.get("container_id") or hosting.container_id or "")
        elif action == "stop":
            hosting.status = "Stopped"
            hosting.cpu_usage_percent = 0.0
            hosting.ram_used_mb = 0
            hosting.uptime_seconds = 0

        msg = str(res.get("message") or f"Action '{action}' executed on isolated container.")
        _record_log(db, hosting.id, "system", msg)
        if action == "start":
            push_notification(db, hosting.user_id, "Hosting Started", f"'{hosting.name}' is now Running.", "Hosting Started")
        elif action == "stop":
            push_notification(db, hosting.user_id, "Hosting Stopped", f"'{hosting.name}' has been Stopped.", "Hosting Stopped")
        db.commit()
        db.refresh(hosting)
        return build_hosting_response(db, hosting)
    except Exception as exc:
        hosting.status = "Error"
        err_detail = str(exc)[:400]
        _record_log(db, hosting.id, "error", f"Lifecycle '{action}' error: {err_detail}")
        push_notification(db, hosting.user_id, "Hosting Error", f"'{hosting.name}' encountered an error: {err_detail}", "Hosting Error")
        db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=err_detail) from exc


@router.post("/{hosting_id}/start", response_model=HostingResponse)
def start_hosting(hosting_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    hosting = _get_owned_hosting(db, hosting_id, current_user)
    return _control_hosting_lifecycle(db, hosting, current_user, "start")


@router.post("/{hosting_id}/stop", response_model=HostingResponse)
def stop_hosting(hosting_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    hosting = _get_owned_hosting(db, hosting_id, current_user)
    return _control_hosting_lifecycle(db, hosting, current_user, "stop")


@router.post("/{hosting_id}/restart", response_model=HostingResponse)
def restart_hosting(hosting_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    hosting = _get_owned_hosting(db, hosting_id, current_user)
    return _control_hosting_lifecycle(db, hosting, current_user, "restart")


@router.post("/{hosting_id}/install-deps", response_model=HostingResponse)
def install_python_dependencies(hosting_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    hosting = _get_owned_hosting(db, hosting_id, current_user)
    if hosting.hosting_type not in ("python_bot", "telegram_bot"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Dependency installation applies to Python & Telegram bots.")
    return _control_hosting_lifecycle(db, hosting, current_user, "install_deps")


@router.put("/{hosting_id}/bot-config", response_model=HostingResponse)
def update_bot_configuration(
    hosting_id: int,
    payload: UpdateBotConfigRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    hosting = _get_owned_hosting(db, hosting_id, current_user)
    if payload.entry_file:
        hosting.entry_file = payload.entry_file.strip()
    if payload.bot_token is not None and payload.bot_token.strip():
        clean_token = payload.bot_token.strip()
        hosting.encrypted_bot_token = encrypt_secret(clean_token)
        hosting.bot_token_masked = mask_secret(clean_token)
    if payload.env_vars is not None:
        hosting.encrypted_env_json = encrypt_secret(json.dumps(payload.env_vars))
    if payload.webhook_enabled is not None:
        hosting.webhook_enabled = payload.webhook_enabled
        if payload.webhook_enabled and not hosting.webhook_secret_path:
            hosting.webhook_secret_path = secrets.token_urlsafe(32)

    _record_log(db, hosting.id, "system", "Updated bot runtime environment & masked token configuration.")
    db.commit()
    db.refresh(hosting)
    return build_hosting_response(db, hosting)


@router.delete("/{hosting_id}")
def delete_hosting(hosting_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    hosting = _get_owned_hosting(db, hosting_id, current_user)
    if hosting.assigned_vps_id:
        vps = db.query(VpsServer).filter(VpsServer.id == hosting.assigned_vps_id).first()
        if vps and vps.connection_status == "Online" and vps.docker_available:
            try:
                client = VpsManagementClient(db)
                client.sync_workspace_and_control_container(vps, "remove", _build_runtime_spec(hosting), None)
            except Exception:
                pass
    staging = get_isolated_staging_dir(hosting.user_id, hosting.id)
    import shutil
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)

    db.delete(hosting)
    db.commit()
    return {"message": "Hosting instance deleted cleanly"}


@router.get("/{hosting_id}/logs", response_model=List[LogEntryResponse])
def get_hosting_logs(
    hosting_id: int,
    log_type: Optional[str] = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    hosting = _get_owned_hosting(db, hosting_id, current_user)

    # Pull fresh live container stdout/stderr from VPS Agent when Online
    if hosting.assigned_vps_id:
        vps = db.query(VpsServer).filter(VpsServer.id == hosting.assigned_vps_id).first()
        if vps and vps.connection_status == "Online" and vps.docker_available:
            try:
                client = VpsManagementClient(db)
                container_name = f"sh_u{hosting.user_id}_h{hosting.id}"
                remote_logs = client.fetch_container_logs(vps, container_name, tail=80)
                stdout_text = str(remote_logs.get("stdout") or "").strip()
                stderr_text = str(remote_logs.get("stderr") or "").strip()
                if stdout_text:
                    _record_log(db, hosting.id, "console", stdout_text[-4000:])
                if stderr_text:
                    _record_log(db, hosting.id, "error", stderr_text[-4000:])
                db.commit()
            except Exception:
                pass

    q = db.query(HostingLog).filter(HostingLog.hosting_id == hosting.id)
    if log_type:
        q = q.filter(HostingLog.log_type == log_type)
    entries = q.order_by(HostingLog.created_at.desc()).limit(150).all()
    return [
        LogEntryResponse(
            id=e.id,
            log_type=e.log_type,
            message=e.message,
            created_at=e.created_at,
        )
        for e in entries
    ]
