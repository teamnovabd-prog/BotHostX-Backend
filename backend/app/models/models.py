from datetime import datetime, timezone
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database.session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    reset_token_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reset_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    profile: Mapped["UserProfile"] = relationship("UserProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    wallet: Mapped["Wallet"] = relationship("Wallet", back_populates="user", uselist=False, cascade="all, delete-orphan")


class RevokedToken(Base):
    __tablename__ = "revoked_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    jti: Mapped[str] = mapped_column(String(96), unique=True, index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class HostingPlan(Base):
    __tablename__ = "hosting_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    price_bdt: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    cpu_limit_cores: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    ram_limit_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=512)
    storage_limit_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=1024)
    max_hosting_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    supports_python: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    supports_php: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    supports_html: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="Active", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    current_plan_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("hosting_plans.id", ondelete="SET NULL"), nullable=True)
    plan_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="profile")
    current_plan: Mapped["HostingPlan | None"] = relationship("HostingPlan")


class VpsServer(Base):
    __tablename__ = "vps_servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(120), nullable=False)
    ip_address: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ssh_port: Mapped[int] = mapped_column(Integer, default=22, nullable=False)
    ssh_username: Mapped[str] = mapped_column(String(64), default="root", nullable=False)
    auth_method: Mapped[str] = mapped_column(String(32), default="vps_agent", nullable=False)
    agent_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    os_name: Mapped[str] = mapped_column(String(120), default="Ubuntu 24.04 LTS", nullable=False)
    region: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    cpu_capacity_cores: Mapped[float] = mapped_column(Float, nullable=False)
    ram_capacity_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_capacity_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_cpu_cores: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    reserved_ram_mb: Mapped[int] = mapped_column(Integer, default=512, nullable=False)
    reserved_storage_mb: Mapped[int] = mapped_column(Integer, default=2048, nullable=False)
    max_hosting_count: Mapped[int] = mapped_column(Integer, default=25, nullable=False)
    runtime_type: Mapped[str] = mapped_column(String(64), default="docker", nullable=False)
    connection_status: Mapped[str] = mapped_column(String(32), default="Setup Required", nullable=False, index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    docker_available: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    maintenance_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    last_health_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class VpsCredentialMetadata(Base):
    """Stores encrypted SSH keys / agent credentials separately from normal VPS metadata."""
    __tablename__ = "vps_credentials_metadata"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    vps_id: Mapped[int] = mapped_column(Integer, ForeignKey("vps_servers.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    credential_type: Mapped[str] = mapped_column(String(32), nullable=False)
    encrypted_secret: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_agent_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    host_key_fingerprint: Mapped[str | None] = mapped_column(String(160), nullable=True)
    key_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    rotated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class VpsHealthCheck(Base):
    __tablename__ = "vps_health_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    vps_id: Mapped[int] = mapped_column(Integer, ForeignKey("vps_servers.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    docker_running: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    os_detected: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


class VpsResourceMetric(Base):
    __tablename__ = "vps_resource_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    vps_id: Mapped[int] = mapped_column(Integer, ForeignKey("vps_servers.id", ondelete="CASCADE"), nullable=False, index=True)
    cpu_usage_percent: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    ram_used_mb: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    disk_used_mb: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    network_rx_mb: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    network_tx_mb: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    uptime_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    running_containers: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    hosting_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


class HostingInstance(Base):
    __tablename__ = "hosting_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    plan_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("hosting_plans.id", ondelete="SET NULL"), nullable=True)
    assigned_vps_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("vps_servers.id", ondelete="RESTRICT"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    hosting_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="Stopped", nullable=False, index=True)
    cpu_limit_cores: Mapped[float] = mapped_column(Float, nullable=False)
    ram_limit_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_limit_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    cpu_usage_percent: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    ram_used_mb: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    storage_used_mb: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    uptime_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    subdomain_slug: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True, index=True)
    public_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    php_version: Mapped[str | None] = mapped_column(String(24), nullable=True)
    entry_file: Mapped[str] = mapped_column(String(120), default="main.py", nullable=False)
    encrypted_bot_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    bot_token_masked: Mapped[str | None] = mapped_column(String(64), nullable=True)
    encrypted_env_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    webhook_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    webhook_secret_path: Mapped[str | None] = mapped_column(String(96), unique=True, nullable=True)
    container_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class VpsHostingAssignment(Base):
    __tablename__ = "vps_hosting_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    hosting_id: Mapped[int] = mapped_column(Integer, ForeignKey("hosting_instances.id", ondelete="CASCADE"), nullable=False, index=True)
    vps_id: Mapped[int] = mapped_column(Integer, ForeignKey("vps_servers.id", ondelete="RESTRICT"), nullable=False, index=True)
    assigned_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    assignment_mode: Mapped[str] = mapped_column(String(32), default="auto", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HostingFileMetadata(Base):
    __tablename__ = "hosting_files_metadata"
    __table_args__ = (
        UniqueConstraint("hosting_id", "relative_path", name="uq_hosting_file_path"),
        Index("ix_hosting_files_hosting_dir", "hosting_id", "parent_dir"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    hosting_id: Mapped[int] = mapped_column(Integer, ForeignKey("hosting_instances.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_dir: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    relative_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(32), nullable=False)
    is_directory: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sha256_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class HostingLog(Base):
    __tablename__ = "hosting_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    hosting_id: Mapped[int] = mapped_column(Integer, ForeignKey("hosting_instances.id", ondelete="CASCADE"), nullable=False, index=True)
    log_type: Mapped[str] = mapped_column(String(24), default="console", nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


class Wallet(Base):
    __tablename__ = "wallets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    balance_bdt: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="BDT", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="wallet")


class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    wallet_id: Mapped[int] = mapped_column(Integer, ForeignKey("wallets.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    tx_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    amount_bdt: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="Completed", nullable=False, index=True)
    reference_id: Mapped[str] = mapped_column(String(96), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


class DepositRequest(Base):
    __tablename__ = "deposit_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    payment_method: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_bdt: Mapped[float] = mapped_column(Float, nullable=False)
    transaction_reference: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    sender_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="Pending", nullable=False, index=True)
    admin_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewed_by_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    deposit_request_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("deposit_requests.id", ondelete="SET NULL"), nullable=True)
    gateway: Mapped[str] = mapped_column(String(48), nullable=False)
    transaction_id: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    amount_bdt: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="BDT", nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="Open", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class SupportMessage(Base):
    __tablename__ = "support_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    ticket_id: Mapped[int] = mapped_column(Integer, ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    is_admin_reply: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


class Announcement(Base):
    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_published: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_by_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class SystemSetting(Base):
    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_name: Mapped[str] = mapped_column(String(100), default="BotHostX", nullable=False)
    hosting_domain: Mapped[str] = mapped_column(String(180), default="", nullable=False)
    api_base_url: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    maintenance_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    max_upload_size_mb: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    allowed_file_types: Mapped[str] = mapped_column(
        String(255),
        default=".py,.php,.html,.css,.js,.json,.txt,.zip,.env",
        nullable=False,
    )
    default_vps_selection_policy: Mapped[str] = mapped_column(String(48), default="most_available_resources", nullable=False)
    hosting_assignment_policy: Mapped[str] = mapped_column(String(48), default="auto", nullable=False)
    bkash_number: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    bkash_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    nagad_number: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    nagad_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    rocket_number: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    rocket_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    manual_payment_instructions: Mapped[str] = mapped_column(
        Text,
        default="Send BDT to our official merchant/personal wallet and submit the TrxID.",
        nullable=False,
    )
    manual_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class VpsOperation(Base):
    __tablename__ = "vps_operations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    vps_id: Mapped[int] = mapped_column(Integer, ForeignKey("vps_servers.id", ondelete="CASCADE"), nullable=False, index=True)
    hosting_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("hosting_instances.id", ondelete="SET NULL"), nullable=True)
    initiated_by_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    operation_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="Pending", nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VpsMigrationJob(Base):
    __tablename__ = "vps_migration_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    hosting_id: Mapped[int] = mapped_column(Integer, ForeignKey("hosting_instances.id", ondelete="CASCADE"), nullable=False, index=True)
    source_vps_id: Mapped[int] = mapped_column(Integer, ForeignKey("vps_servers.id", ondelete="RESTRICT"), nullable=False)
    target_vps_id: Mapped[int] = mapped_column(Integer, ForeignKey("vps_servers.id", ondelete="RESTRICT"), nullable=False)
    initiated_by_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="Pending", nullable=False, index=True)
    preserve_url: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VpsOperationLog(Base):
    __tablename__ = "vps_operation_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    vps_id: Mapped[int] = mapped_column(Integer, ForeignKey("vps_servers.id", ondelete="CASCADE"), nullable=False, index=True)
    operation_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("vps_operations.id", ondelete="SET NULL"), nullable=True)
    migration_job_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("vps_migration_jobs.id", ondelete="SET NULL"), nullable=True)
    level: Mapped[str] = mapped_column(String(16), default="INFO", nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
