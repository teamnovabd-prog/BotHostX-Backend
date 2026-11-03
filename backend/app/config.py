from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "SmartHost"
    DATABASE_URL: str = "postgresql://smarthost_user:CHANGE_ME@localhost:5432/smarthost"
    REDIS_URL: str = "redis://localhost:6379/0"
    SECRET_KEY: str = "CHANGE_ME_TO_A_LONG_RANDOM_SECRET"
    ADMIN_EMAIL: str = "teamnovabd@gmail.com"
    ADMIN_PASSWORD: str = ""
    API_BASE_URL: str = "https://api.example.com"
    HOSTING_DOMAIN: str = "example.com"
    VPS_ENCRYPTION_KEY: str = ""
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    MAX_UPLOAD_SIZE_MB: int = 50
    WORKER_POLL_INTERVAL_SECONDS: int = 30
    STAGING_ROOT: str = "/tmp/smarthost/staging"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def normalized_database_url(self) -> str:
        """Normalizes Render's postgres:// connection strings for SQLAlchemy 2.0 compatibility."""
        raw = self.DATABASE_URL.strip()
        if raw.startswith("postgres://"):
            return "postgresql+psycopg2://" + raw[len("postgres://"):]
        if raw.startswith("postgresql://"):
            return "postgresql+psycopg2://" + raw[len("postgresql://"):]
        return raw


settings = Settings()
