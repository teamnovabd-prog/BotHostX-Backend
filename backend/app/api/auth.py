from datetime import timedelta
import secrets
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.database.session import get_db
from app.models.models import HostingPlan, RevokedToken, User, UserProfile, Wallet, utcnow
from app.schemas.schemas import (
    AuthTokenResponse,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    UpdateProfileRequest,
    UserResponse,
)
from app.security.auth import (
    ALGORITHM,
    _jwt_signing_key,
    bearer_scheme,
    create_access_token,
    enforce_rate_limit,
    get_current_user,
    hash_password,
    hash_reset_code,
    verify_password,
)
from app.services.serializers import build_user_response, push_notification

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


@router.post("/register", response_model=AuthTokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    enforce_rate_limit(request, max_requests=10, window_seconds=60)
    normalized_email = payload.email.strip().lower()
    if normalized_email == settings.ADMIN_EMAIL.strip().lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This email address is reserved for system administration.",
        )

    existing = db.query(User).filter(User.email == normalized_email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email address already exists.",
        )

    free_plan = (
        db.query(HostingPlan)
        .filter(HostingPlan.price_bdt == 0.0, HostingPlan.status == "Active")
        .order_by(HostingPlan.id.asc())
        .first()
    )

    user = User(
        email=normalized_email,
        password_hash=hash_password(payload.password),
        is_admin=False,
        is_active=True,
    )
    db.add(user)
    db.flush()

    expires = utcnow() + timedelta(days=free_plan.duration_days) if free_plan else None
    profile = UserProfile(
        user_id=user.id,
        full_name=payload.full_name.strip(),
        phone=payload.phone.strip() if payload.phone else None,
        current_plan_id=free_plan.id if free_plan else None,
        plan_expires_at=expires,
    )
    wallet = Wallet(user_id=user.id, balance_bdt=0.0, currency="BDT")
    db.add(profile)
    db.add(wallet)
    push_notification(
        db,
        user.id,
        "Welcome to BotHostX",
        "Your cloud hosting account is ready. Select a plan or deploy your first bot or site.",
        "Announcement",
    )
    db.commit()
    db.refresh(user)

    token = create_access_token(user.id, user.is_admin)
    return AuthTokenResponse(access_token=token, user=build_user_response(db, user))


@router.post("/login", response_model=AuthTokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    enforce_rate_limit(request, max_requests=12, window_seconds=60)
    normalized_email = payload.email.strip().lower()
    user = db.query(User).filter(User.email == normalized_email).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is suspended. Contact support for assistance.",
        )

    token = create_access_token(user.id, user.is_admin)
    return AuthTokenResponse(access_token=token, user=build_user_response(db, user))


@router.post("/logout")
def logout(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if credentials and credentials.credentials:
        try:
            payload = jwt.decode(credentials.credentials, _jwt_signing_key(), algorithms=[ALGORITHM])
            jti = payload.get("jti")
            if jti and not db.query(RevokedToken).filter(RevokedToken.jti == jti).first():
                db.add(RevokedToken(jti=jti, user_id=current_user.id))
                db.commit()
        except JWTError:
            pass
    return {"message": "Logged out successfully"}


@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, request: Request, db: Session = Depends(get_db)):
    enforce_rate_limit(request, max_requests=6, window_seconds=60)
    normalized_email = payload.email.strip().lower()
    user = db.query(User).filter(User.email == normalized_email).first()
    if user:
        raw_code = f"{secrets.randbelow(900000) + 100000}"
        user.reset_token_hash = hash_reset_code(raw_code)
        user.reset_token_expires_at = utcnow() + timedelta(minutes=15)
        db.commit()
        # In production SMTP/Transactional Mailer dispatches raw_code exclusively to user.email
    return {
        "message": "If an account matches that email, a 6-digit password reset code has been dispatched."
    }


@router.post("/reset-password")
def reset_password(payload: ResetPasswordRequest, request: Request, db: Session = Depends(get_db)):
    enforce_rate_limit(request, max_requests=6, window_seconds=60)
    user = db.query(User).filter(User.email == payload.email.strip().lower()).first()
    if (
        not user
        or not user.reset_token_hash
        or not user.reset_token_expires_at
        or user.reset_token_expires_at < utcnow()
        or user.reset_token_hash != hash_reset_code(payload.reset_code.strip())
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired password reset code.",
        )
    user.password_hash = hash_password(payload.new_password)
    user.reset_token_hash = None
    user.reset_token_expires_at = None
    db.commit()
    return {"message": "Password reset successfully. Please sign in with your new password."}


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )
    current_user.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"message": "Password updated successfully."}


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return build_user_response(db, current_user)


@router.put("/profile", response_model=UserResponse)
def update_profile(
    payload: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = db.query(UserProfile).filter(UserProfile.user_id == current_user.id).first()
    if profile is None:
        profile = UserProfile(user_id=current_user.id, full_name=payload.full_name.strip())
        db.add(profile)
    else:
        profile.full_name = payload.full_name.strip()
        profile.phone = payload.phone.strip() if payload.phone else None
        profile.avatar_url = payload.avatar_url.strip() if payload.avatar_url else None
    db.commit()
    return build_user_response(db, current_user)
