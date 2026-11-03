from datetime import datetime
from typing import Dict, List, Optional
from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    phone: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    reset_code: str = Field(..., min_length=6, max_length=64)
    new_password: str = Field(..., min_length=8, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8, max_length=128)


class UpdateProfileRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=120)
    phone: Optional[str] = None
    avatar_url: Optional[str] = None


class UserResponse(BaseModel):
    id: int
    email: str
    full_name: str
    phone: Optional[str] = None
    avatar_url: Optional[str] = None
    is_admin: bool
    is_active: bool
    current_plan_name: Optional[str] = None
    plan_expires_at: Optional[datetime] = None
    wallet_balance_bdt: float = 0.0
    hosting_count: int = 0
    created_at: datetime


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class PlanResponse(BaseModel):
    id: int
    name: str
    price_bdt: float
    duration_days: int
    cpu_limit_cores: float
    ram_limit_mb: int
    storage_limit_mb: int
    max_hosting_count: int
    supports_python: bool
    supports_php: bool
    supports_html: bool
    status: str


class PlanWriteRequest(BaseModel):
    name: str
    price_bdt: float = Field(..., ge=0)
    duration_days: int = Field(..., ge=1)
    cpu_limit_cores: float = Field(..., gt=0)
    ram_limit_mb: int = Field(..., ge=64)
    storage_limit_mb: int = Field(..., ge=100)
    max_hosting_count: int = Field(..., ge=1)
    supports_python: bool = True
    supports_php: bool = True
    supports_html: bool = True
    status: str = "Active"


class PurchasePlanRequest(BaseModel):
    plan_id: int


class CreateHostingRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    hosting_type: str = Field(..., pattern="^(python_bot|telegram_bot|php_script|html_web)$")
    subdomain_slug: Optional[str] = Field(default=None, max_length=64)
    entry_file: str = "main.py"
    bot_token: Optional[str] = None
    env_vars: Dict[str, str] = Field(default_factory=dict)
    webhook_enabled: bool = False


class UpdateBotConfigRequest(BaseModel):
    entry_file: Optional[str] = None
    bot_token: Optional[str] = None
    env_vars: Optional[Dict[str, str]] = None
    webhook_enabled: Optional[bool] = None


class HostingResponse(BaseModel):
    id: int
    user_id: int
    name: str
    hosting_type: str
    status: str
    plan_name: Optional[str] = None
    assigned_vps_id: Optional[int] = None
    assigned_vps_name: Optional[str] = None
    assigned_vps_region: Optional[str] = None
    cpu_limit_cores: float
    ram_limit_mb: int
    storage_limit_mb: int
    cpu_usage_percent: float
    ram_used_mb: int
    storage_used_mb: float
    uptime_seconds: int
    public_url: Optional[str] = None
    php_version: Optional[str] = None
    entry_file: str
    bot_token_masked: Optional[str] = None
    webhook_enabled: bool = False
    created_at: datetime
    expires_at: datetime


class FileItemResponse(BaseModel):
    id: int
    relative_path: str
    parent_dir: str
    file_name: str
    file_type: str
    is_directory: bool
    size_bytes: int
    updated_at: datetime


class CreateFolderRequest(BaseModel):
    folder_path: str = Field(..., min_length=1, max_length=255)


class RenameFileRequest(BaseModel):
    old_path: str
    new_name: str = Field(..., min_length=1, max_length=128)


class ExtractZipRequest(BaseModel):
    zip_path: str


class FileContentUpdateRequest(BaseModel):
    relative_path: str
    content: str


class LogEntryResponse(BaseModel):
    id: int
    log_type: str
    message: str
    created_at: datetime


class ActivityItem(BaseModel):
    title: str
    subtitle: str
    category: str
    timestamp: datetime


class DashboardResponse(BaseModel):
    user: UserResponse
    active_hosting_count: int
    total_hosting_count: int
    current_plan: Optional[PlanResponse] = None
    overall_status: str
    cpu_usage_percent: float
    ram_used_mb: int
    ram_limit_mb: int
    storage_used_mb: float
    storage_limit_mb: int
    nearest_expiry: Optional[datetime] = None
    unread_notifications: int = 0
    recent_activities: List[ActivityItem] = Field(default_factory=list)
    announcements: List[Dict[str, str]] = Field(default_factory=list)


class PaymentMethodInfo(BaseModel):
    method: str
    enabled: bool
    account_number: str
    instructions: str


class WalletOverviewResponse(BaseModel):
    balance_bdt: float
    currency: str = "BDT"
    payment_methods: List[PaymentMethodInfo]


class DepositCreateRequest(BaseModel):
    payment_method: str = Field(..., pattern="^(bKash|Nagad|Rocket|Manual)$")
    amount_bdt: float = Field(..., gt=0)
    transaction_reference: str = Field(..., min_length=4, max_length=120)
    sender_number: Optional[str] = None


class DepositResponse(BaseModel):
    id: int
    user_id: int
    user_email: Optional[str] = None
    payment_method: str
    amount_bdt: float
    transaction_reference: str
    sender_number: Optional[str] = None
    status: str
    admin_note: Optional[str] = None
    created_at: datetime
    reviewed_at: Optional[datetime] = None


class WalletTransactionResponse(BaseModel):
    id: int
    tx_type: str
    amount_bdt: float
    status: str
    reference_id: str
    description: str
    created_at: datetime


class SupportTicketCreateRequest(BaseModel):
    subject: str = Field(..., min_length=3, max_length=180)
    description: str = Field(..., min_length=5, max_length=4000)


class SupportReplyRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    new_status: Optional[str] = None


class SupportMessageResponse(BaseModel):
    id: int
    sender_user_id: int
    is_admin_reply: bool
    message: str
    created_at: datetime


class SupportTicketResponse(BaseModel):
    id: int
    user_id: int
    user_email: Optional[str] = None
    subject: str
    description: str
    status: str
    created_at: datetime
    updated_at: datetime
    messages: List[SupportMessageResponse] = Field(default_factory=list)


class NotificationResponse(BaseModel):
    id: int
    title: str
    message: str
    category: str
    is_read: bool
    created_at: datetime


class VpsCreateUpdateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    provider: str = Field(..., min_length=2, max_length=120)
    ip_address: str = Field(..., min_length=3, max_length=64)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_username: str = Field(default="root", min_length=1, max_length=64)
    auth_method: str = Field(default="vps_agent", pattern="^(vps_agent|ssh_key|ssh_password)$")
    agent_url: Optional[str] = None
    secret_credential: Optional[str] = None
    agent_token: Optional[str] = None
    host_key_fingerprint: Optional[str] = None
    os_name: str = "Ubuntu 24.04 LTS"
    region: str = "Singapore"
    cpu_capacity_cores: float = Field(..., gt=0)
    ram_capacity_mb: int = Field(..., ge=256)
    storage_capacity_mb: int = Field(..., ge=1024)
    reserved_cpu_cores: float = Field(default=0.5, ge=0)
    reserved_ram_mb: int = Field(default=512, ge=0)
    reserved_storage_mb: int = Field(default=2048, ge=0)
    max_hosting_count: int = Field(default=25, ge=1)
    runtime_type: str = "docker"
    is_enabled: bool = True
    maintenance_mode: bool = False


class VpsResponse(BaseModel):
    id: int
    name: str
    provider: str
    ip_address: str
    ssh_port: int
    ssh_username: str
    auth_method: str
    agent_url: Optional[str] = None
    os_name: str
    region: str
    cpu_capacity_cores: float
    available_cpu_cores: float
    ram_capacity_mb: int
    available_ram_mb: int
    storage_capacity_mb: int
    available_storage_mb: int
    reserved_cpu_cores: float
    reserved_ram_mb: int
    reserved_storage_mb: int
    max_hosting_count: int
    hosting_count: int
    running_containers: int
    cpu_usage_percent: float
    ram_used_mb: int
    disk_used_mb: int
    uptime_seconds: int
    runtime_type: str
    connection_status: str
    last_error: Optional[str] = None
    docker_available: bool
    is_enabled: bool
    maintenance_mode: bool
    has_credentials_configured: bool = False
    last_health_check_at: Optional[datetime] = None


class VpsClusterSummaryResponse(BaseModel):
    total_vps: int
    online_vps: int
    offline_vps: int
    unreachable_vps: int
    total_cpu_cores: float
    available_cpu_cores: float
    total_ram_mb: int
    available_ram_mb: int
    total_storage_mb: int
    available_storage_mb: int
    total_hosted_containers: int
    total_running_hosting: int


class AssignVpsRequest(BaseModel):
    vps_id: int


class MigrateVpsRequest(BaseModel):
    target_vps_id: int
    preserve_url: bool = True


class MaintenanceToggleRequest(BaseModel):
    maintenance_mode: bool


class AdminDashboardResponse(BaseModel):
    total_users: int
    active_users: int
    total_hosting: int
    running_hosting: int
    stopped_hosting: int
    pending_deposits: int
    total_revenue_bdt: float
    vps_summary: VpsClusterSummaryResponse


class DepositReviewRequest(BaseModel):
    approve: bool
    admin_note: Optional[str] = None


class AnnouncementWriteRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=180)
    content: str = Field(..., min_length=5, max_length=4000)
    is_published: bool = True


class SystemSettingsUpdateRequest(BaseModel):
    app_name: str
    hosting_domain: str
    api_base_url: str
    maintenance_mode: bool
    max_upload_size_mb: int = Field(..., ge=1, le=500)
    allowed_file_types: str
    default_vps_selection_policy: str
    hosting_assignment_policy: str
    bkash_number: str = ""
    bkash_enabled: bool = True
    nagad_number: str = ""
    nagad_enabled: bool = True
    rocket_number: str = ""
    rocket_enabled: bool = True
    manual_payment_instructions: str = ""
    manual_enabled: bool = True
