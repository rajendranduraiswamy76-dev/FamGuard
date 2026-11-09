from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Runtime
    env: Literal["dev", "test", "prod"] = "dev"
    run_mode: Literal["api", "worker", "combined"] = "api"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    fake_externals: bool = False

    # Postgres
    database_url: str
    db_pool_min_size: int = Field(default=1, ge=1)
    db_pool_max_size: int = Field(default=5, ge=1)
    db_connect_max_attempts: int = Field(default=5, ge=1)
    db_connect_base_delay_s: float = Field(default=0.5, gt=0)

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # OpenAI / Pinecone
    openai_api_key: SecretStr
    pinecone_api_key: SecretStr
    pinecone_index: str = "famguard-vault"
    pinecone_namespace: str = "family-vault"

    # Cloudflare R2
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: SecretStr = SecretStr("")
    r2_bucket: str = "famguard-vault"
    # Dev only: persist originals locally; offline vectors also use this shared volume.
    local_storage_dir: str = ""

    # Clerk
    clerk_jwks_url: str = ""
    clerk_issuer: str = ""
    allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # Resend / worker / cron
    resend_api_key: SecretStr = SecretStr("")
    alert_from_email: str = ""
    # Resend free tier allows 100 emails/day; keep headroom for manual sends.
    alert_daily_send_cap: int = Field(default=95, ge=1)
    # Dev only: deliver alert emails to an SMTP catcher such as Mailpit (smtp://host:port).
    smtp_url: str = ""
    celery_timezone: str = "UTC"
    cron_secret: SecretStr = SecretStr("")

    @field_validator("database_url")
    @classmethod
    def _postgres_scheme(cls, value: str) -> str:
        if not value.startswith(("postgresql://", "postgres://")):
            raise ValueError("DATABASE_URL must use the postgresql:// or postgres:// scheme")
        return value

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _validate_environment(self) -> Settings:
        if self.db_pool_min_size > self.db_pool_max_size:
            raise ValueError("DB_POOL_MIN_SIZE cannot exceed DB_POOL_MAX_SIZE")
        if not self.fake_externals:
            missing_ai_keys = [
                name for name, secret, placeholder in (
                    ("OPENAI_API_KEY", self.openai_api_key, "sk-fake"),
                    ("PINECONE_API_KEY", self.pinecone_api_key, "pc-fake"),
                )
                if not secret.get_secret_value().strip()
                or secret.get_secret_value().strip() == placeholder
            ]
            if missing_ai_keys:
                raise ValueError(
                    "Real services require configured keys: " + ", ".join(missing_ai_keys)
                )
        if self.env == "prod":
            if self.fake_externals:
                raise ValueError("FAKE_EXTERNALS is not allowed when ENV=prod")
            if self.smtp_url or self.local_storage_dir:
                raise ValueError("SMTP_URL and LOCAL_STORAGE_DIR are dev-only settings")
            required = {
                "CLERK_JWKS_URL": self.clerk_jwks_url,
                "CLERK_ISSUER": self.clerk_issuer,
                "R2_ACCOUNT_ID": self.r2_account_id,
                "R2_ACCESS_KEY_ID": self.r2_access_key_id,
                "R2_SECRET_ACCESS_KEY": self.r2_secret_access_key.get_secret_value(),
                "CRON_SECRET": self.cron_secret.get_secret_value(),
            }
            if self.run_mode in ("worker", "combined"):
                required["RESEND_API_KEY"] = self.resend_api_key.get_secret_value()
                required["ALERT_FROM_EMAIL"] = self.alert_from_email
            missing = sorted(name for name, val in required.items() if not val)
            if missing:
                raise ValueError(f"Missing required settings for ENV=prod: {', '.join(missing)}")
            if not self.allowed_origins or "*" in self.allowed_origins:
                raise ValueError("ALLOWED_ORIGINS must list explicit origins when ENV=prod")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
