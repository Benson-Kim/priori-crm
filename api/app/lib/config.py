"""Application configuration with validation and environment management."""

import secrets
from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, PostgresDsn, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables with validation."""

    APP_NAME: str = "Business Central"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: Literal["development", "test", "staging", "production"] = "production"
    DEBUG: bool = Field(default=False)

    # API
    API_V1_PREFIX: str = "/api/v1"

    # Organization-local accounting cutoffs. This deployment is Kenya-based;
    # override explicitly for another dedicated organization deployment.
    REPORTING_TIMEZONE: str = "Africa/Nairobi"

    # Database
    DATABASE_URL: PostgresDsn
    DB_POOL_SIZE: int = Field(default=20, ge=5, le=100)
    DB_MAX_OVERFLOW: int = Field(default=10, ge=0, le=50)
    DB_POOL_TIMEOUT: int = Field(default=30, ge=10, le=60)
    DB_POOL_RECYCLE: int = Field(default=3600, ge=300)
    DB_ECHO: bool = Field(default=False)

    # JWT
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=30, ge=5, le=1440)
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=7, ge=1, le=30)

    # AWS SES
    AWS_REGION: str = "eu-north-1"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    SES_SENDER_EMAIL: str = "noreply@example.com"
    SES_MAX_RETRIES: int = Field(default=3, ge=1, le=5)

    # CORS
    CORS_ORIGINS: str = (
        "https://priori-crm-ou38.vercel.app,http://localhost:3000,http://localhost:5173"
    )
    CORS_ORIGIN_REGEX: str | None = r"^https://priori-crm-ou38.*\.vercel\.app$"
    CORS_ALLOW_METHODS: str = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
    CORS_ALLOW_HEADERS: str = (
        "Authorization,Content-Type,X-Request-ID,X-Internal-Secret"
    )

    # Rate Limiting
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_PER_MINUTE: int = Field(default=60, ge=10, le=1000)
    AUTH_MAX_OTP_ATTEMPTS: int = Field(default=5, ge=1, le=20)
    AUTH_MAX_RESET_ATTEMPTS: int = Field(default=5, ge=1, le=20)
    AUTH_LOGIN_MAX_ATTEMPTS: int = Field(default=10, ge=3, le=100)
    AUTH_LOGIN_WINDOW_SECONDS: int = Field(default=300, ge=30, le=3600)

    # Password reset (forgot-password flow)
    PASSWORD_RESET_EXPIRE_MINUTES: int = Field(default=30, ge=5, le=120)
    # Base URL of the frontend, used to build the password-reset link in the
    # email (e.g. https://app.example.com -> <base>/reset-password?token=...).
    FRONTEND_BASE_URL: str = "http://localhost:5173"
    RATE_LIMIT_TRUST_FORWARDED_FOR: bool = False
    RATE_LIMIT_BACKEND: Literal["memory", "redis"] = "memory"

    # Operator MFA (ADR-0014): TOTP second factor + step-up for the
    # /platform console. Drift = accepted clock skew in 30 s steps; attempt
    # throttling shares the auth RateLimitStore backend (per-user bucket).
    MFA_TOTP_DRIFT_STEPS: int = Field(default=1, ge=0, le=2)
    MFA_MAX_ATTEMPTS: int = Field(default=5, ge=3, le=20)
    MFA_ATTEMPT_WINDOW_SECONDS: int = Field(default=300, ge=30, le=3600)
    # Optional dedicated Fernet key (urlsafe-base64, 32 bytes) for TOTP-seed
    # encryption at rest; empty = derived from JWT_SECRET_KEY (ADR-0014
    # documents the trade-off). Recommended set in production.
    MFA_ENCRYPTION_KEY: str = ""
    # Interim compensating control (ADR-0014): comma-separated CIDRs allowed
    # to reach /platform. Empty = disabled. Malformed entries fail here at
    # startup (fail closed) rather than silently disabling the check.
    PLATFORM_IP_ALLOWLIST: str = ""
    TOKEN_DENYLIST_BACKEND: Literal["memory", "redis"] = "memory"  # noqa: S105 — backend name, not a secret
    REDIS_URL: str = ""

    # Monitoring
    SENTRY_DSN: str = ""
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # OpenTelemetry metrics (observability follow-up, issue #4). Off by
    # default; when enabled, HTTP server metrics are exported over
    # OTLP/HTTP to OTEL_EXPORTER_OTLP_ENDPOINT (or the SDK default).
    OTEL_METRICS_ENABLED: bool = False
    OTEL_EXPORTER_OTLP_ENDPOINT: str = ""
    OTEL_METRIC_EXPORT_INTERVAL_MS: int = Field(default=60000, ge=1000, le=300000)

    # Batch Processing
    BATCH_SIZE: int = Field(default=1000, ge=100, le=10000)
    BATCH_TIMEOUT_SECONDS: int = Field(default=300, ge=60, le=3600)

    # Exports: cap how many heavy (Excel/PDF) generations run concurrently
    # per process and reject oversized synchronous report workbooks.
    EXPORT_MAX_CONCURRENCY: int = Field(default=4, ge=1, le=64)
    REPORT_EXPORT_MAX_ROWS: int = Field(default=100_000, ge=1, le=1_000_000)
    TAX_REPORT_EXPORT_MAX_ROWS: int = Field(default=10_000, ge=1, le=100_000)

    # File Storage
    UPLOAD_DIR: str = "uploads"
    STORAGE_BACKEND: Literal["local", "s3"] = "local"
    S3_BUCKET: str = ""
    S3_REGION: str = ""
    S3_ENDPOINT_URL: str = ""
    S3_PRESIGN_EXPIRY: int = Field(default=900, ge=60, le=86400)

    INTERNAL_API_SECRET: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def validate_jwt_secret(cls, v: str) -> str:
        """Ensure JWT secret is sufficiently secure."""
        if len(v) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters long")

        insecure_defaults = [
            "your-super-secret-key-change-in-production",
            "secret",
            "change-me",
        ]
        if v.lower() in insecure_defaults:
            raise ValueError("JWT_SECRET_KEY must not be a default/insecure value")

        return v

    @field_validator("MFA_ENCRYPTION_KEY")
    @classmethod
    def validate_mfa_encryption_key(cls, v: str) -> str:
        """Reject a malformed Fernet key at startup (fail closed).

        A key that Fernet would refuse must stop the application from
        booting, not surface later as undecryptable TOTP seeds.
        """
        if not v:
            return v
        import base64

        try:
            decoded = base64.urlsafe_b64decode(v.encode("ascii"))
        except Exception as exc:  # binascii.Error / UnicodeEncodeError
            raise ValueError(
                "MFA_ENCRYPTION_KEY must be a urlsafe-base64-encoded 32-byte key"
            ) from exc
        if len(decoded) != 32:
            raise ValueError(
                "MFA_ENCRYPTION_KEY must decode to exactly 32 bytes (Fernet key)"
            )
        return v

    @field_validator("PLATFORM_IP_ALLOWLIST")
    @classmethod
    def validate_platform_ip_allowlist(cls, v: str) -> str:
        """Reject malformed CIDR entries at startup (fail closed, ADR-0014).

        An unparseable allowlist must never degrade to "check disabled":
        the application refuses to boot instead.
        """
        import ipaddress

        for entry in (item.strip() for item in v.split(",")):
            if not entry:
                continue
            try:
                ipaddress.ip_network(entry, strict=False)
            except ValueError as exc:
                raise ValueError(
                    f"PLATFORM_IP_ALLOWLIST entry {entry!r} is not a valid "
                    "IP network (CIDR)"
                ) from exc
        return v

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, v: PostgresDsn) -> PostgresDsn:
        """Ensure database URL is valid PostgreSQL connection."""
        if not str(v).startswith(("postgresql://", "postgresql+psycopg2://")):
            raise ValueError("DATABASE_URL must be a valid PostgreSQL URL")
        return v

    @field_validator("REPORTING_TIMEZONE")
    @classmethod
    def validate_reporting_timezone(cls, value: str) -> str:
        """Reject invalid IANA timezone names at application startup."""
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                "REPORTING_TIMEZONE must be a valid IANA timezone"
            ) from exc
        return value

    @model_validator(mode="after")
    def validate_production_hardening(self) -> "Settings":
        """Fail fast on insecure configuration in production."""
        if self.ENVIRONMENT != "production":
            return self

        errors: list[str] = []

        if self.DEBUG:
            errors.append("DEBUG must be False in production")

        if not self.AWS_ACCESS_KEY_ID or not self.AWS_SECRET_ACCESS_KEY:
            errors.append("AWS SES credentials are required in production")

        if not self.SES_SENDER_EMAIL or self.SES_SENDER_EMAIL == "noreply@example.com":
            errors.append("SES_SENDER_EMAIL must be configured in production")

        if self.STORAGE_BACKEND == "s3" and not self.S3_BUCKET:
            errors.append("S3_BUCKET must be configured when STORAGE_BACKEND is 's3'")

        # A multi-worker / horizontally-scaled production deployment must use
        # the shared Redis window, otherwise the limit is per-process.
        # Enforce Redis in production so the limit isn't silently
        # multiplied by the worker count, and require REDIS_URL for it.
        if self.RATE_LIMIT_ENABLED and self.RATE_LIMIT_BACKEND != "redis":
            errors.append(
                "RATE_LIMIT_BACKEND must be 'redis' in production so the rate "
                "limit is shared across workers (set RATE_LIMIT_ENABLED=false "
                "only for a single-process deployment)"
            )

        if (
            self.RATE_LIMIT_ENABLED
            and self.RATE_LIMIT_BACKEND == "redis"
            and not self.REDIS_URL
        ):
            errors.append("REDIS_URL is required when RATE_LIMIT_BACKEND='redis'")

        # The refresh-token denylist must be shared in a multi-worker /
        # horizontally-scaled deployment, otherwise a token revoked on one
        # worker is still accepted by another.
        if self.TOKEN_DENYLIST_BACKEND == "redis" and not self.REDIS_URL:  # noqa: S105 — backend name, not a secret
            errors.append("REDIS_URL is required when TOKEN_DENYLIST_BACKEND='redis'")

        if errors:
            raise ValueError("Insecure production configuration: " + "; ".join(errors))

        return self

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse comma-separated CORS origins into list."""
        return [
            origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()
        ]

    @property
    def cors_allow_methods_list(self) -> list[str]:
        """Parse comma-separated allowed CORS methods into a list."""
        return [m.strip() for m in self.CORS_ALLOW_METHODS.split(",") if m.strip()]

    @property
    def cors_allow_headers_list(self) -> list[str]:
        """Parse comma-separated allowed CORS headers into a list."""
        return [h.strip() for h in self.CORS_ALLOW_HEADERS.split(",") if h.strip()]

    @property
    def is_development(self) -> bool:
        """Check if running in development mode."""
        return self.ENVIRONMENT == "development"

    @property
    def is_production(self) -> bool:
        """Check if running in production mode."""
        return self.ENVIRONMENT == "production"

    @classmethod
    def generate_secret_key(cls) -> str:
        """Generate a secure random secret key."""
        return secrets.token_urlsafe(32)


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance to avoid re-reading env vars."""
    return Settings()


settings = get_settings()
