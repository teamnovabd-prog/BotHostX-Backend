from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, admin_vps, auth, dashboard, files, hosting, plans_wallet, support_notifications
from app.config import settings
from app.database.session import Base, SessionLocal, engine
from app.models.models import HostingPlan, SystemSetting, User, UserProfile, Wallet
from app.security.auth import hash_password

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("smarthost.startup")


def bootstrap_initial_admin_and_settings() -> None:
    """
    Initial Admin Setup:
    1. Creates tables automatically if not already created by Alembic.
    2. Seeds default SystemSetting and HostingPlans if empty.
    3. Creates initial Admin account (ADMIN_EMAIL / ADMIN_PASSWORD) if missing, without overwriting existing password.
    """
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        sys_cfg = db.query(SystemSetting).first()
        if sys_cfg is None:
            sys_cfg = SystemSetting(
                id=1,
                app_name=settings.APP_NAME or "BotHostX",
                hosting_domain=settings.HOSTING_DOMAIN if settings.HOSTING_DOMAIN != "example.com" else "",
                api_base_url=settings.API_BASE_URL if settings.API_BASE_URL != "https://api.example.com" else "",
                maintenance_mode=False,
            )
            db.add(sys_cfg)

        if db.query(HostingPlan).count() == 0:
            db.add_all(
                [
                    HostingPlan(
                        name="Free Plan",
                        price_bdt=0.0,
                        duration_days=7,
                        cpu_limit_cores=0.25,
                        ram_limit_mb=256,
                        storage_limit_mb=512,
                        max_hosting_count=1,
                        supports_python=True,
                        supports_php=True,
                        supports_html=True,
                        status="Active",
                    ),
                    HostingPlan(
                        name="Basic Plan",
                        price_bdt=199.0,
                        duration_days=30,
                        cpu_limit_cores=0.5,
                        ram_limit_mb=512,
                        storage_limit_mb=2048,
                        max_hosting_count=3,
                        supports_python=True,
                        supports_php=True,
                        supports_html=True,
                        status="Active",
                    ),
                    HostingPlan(
                        name="Standard Plan",
                        price_bdt=499.0,
                        duration_days=30,
                        cpu_limit_cores=1.0,
                        ram_limit_mb=1024,
                        storage_limit_mb=5120,
                        max_hosting_count=6,
                        supports_python=True,
                        supports_php=True,
                        supports_html=True,
                        status="Active",
                    ),
                    HostingPlan(
                        name="Premium Plan",
                        price_bdt=999.0,
                        duration_days=30,
                        cpu_limit_cores=2.0,
                        ram_limit_mb=2048,
                        storage_limit_mb=15360,
                        max_hosting_count=15,
                        supports_python=True,
                        supports_php=True,
                        supports_html=True,
                        status="Active",
                    ),
                ]
            )

        admin_email = settings.ADMIN_EMAIL.strip().lower()
        admin_password = settings.ADMIN_PASSWORD.strip()
        if admin_email:
            existing_admin = db.query(User).filter(User.email == admin_email).first()
            if existing_admin is None:
                if admin_password and not admin_password.startswith("CHANGE_"):
                    admin_user = User(
                        email=admin_email,
                        password_hash=hash_password(admin_password),
                        is_admin=True,
                        is_active=True,
                    )
                    db.add(admin_user)
                    db.flush()
                    db.add(UserProfile(user_id=admin_user.id, full_name="SmartHost Administrator"))
                    db.add(Wallet(user_id=admin_user.id, balance_bdt=0.0, currency="BDT"))
                    logger.info("Created initial Admin account for %s", admin_email)
                else:
                    logger.warning(
                        "ADMIN_PASSWORD is not configured yet; skipping initial Admin creation."
                    )
            else:
                logger.info("Initial Admin account (%s) already exists; preserving existing password.", admin_email)

        db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap_initial_admin_and_settings()
    yield


app = FastAPI(
    title="BotHostX / SmartHost Cloud API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["Root"])
def root_info():
    return {
        "service": "smarthost-api",
        "platform": settings.APP_NAME,
        "status": "running",
        "health_endpoint": "/health",
        "auth_endpoints": ["/api/auth/register", "/api/auth/login", "/api/auth/me"],
    }


app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(hosting.router)
app.include_router(files.router)
app.include_router(plans_wallet.router)
app.include_router(support_notifications.router)
app.include_router(admin.router)
app.include_router(admin_vps.router)
