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

    # Context-aware access control (ABAC + zero trust, issue #67).
    # The policy engine evaluates per-request context (time of day,
    # geolocation, device fingerprint, IP reputation, resource sensitivity)
    # on top of the existing RBAC gates; it can only restrict, never widen.
    ABAC_ENABLED: bool = True
    # Trust edge-supplied context headers (X-Geo-Country/-Lat/-Lon,
    # X-Device-Fingerprint). Enable ONLY behind a proxy/CDN that sets and
    # strips these headers; otherwise they are attacker-controlled.
    ABAC_TRUST_CONTEXT_HEADERS: bool = False
    # Comma-separated bad-reputation sources: exact IPs, CIDR ranges, or
    # literal client identifiers. Matching requests are denied outright.
    ABAC_IP_DENYLIST: str = ""
    # Comma-separated ISO country codes to refuse (requires a geo signal).
    ABAC_GEO_BLOCKLIST: str = ""
    # Off-hours window in the organisation's REPORTING_TIMEZONE: sensitive
    # access inside it triggers an OTP step-up challenge. start == end
    # disables the window (the test suite pins 0/0 for determinism).
    ABAC_OFF_HOURS_START: int = Field(default=22, ge=0, le=23)
    ABAC_OFF_HOURS_END: int = Field(default=6, ge=0, le=23)
    # How long a completed OTP step-up satisfies the static context rules.
    # Sized to a WORK SHIFT, not a transaction: 30min across a 22h→6h night
    # would mean ~16 OTP emails, which is nagging rather than security. The
    # guarantee is unchanged at any TTL — an attacker holding a stolen token
    # has no inbox, so they can never mint a fresh `sua` claim at all.
    ABAC_STEP_UP_TTL_MINUTES: int = Field(default=480, ge=1, le=1440)
    # Audit ALLOW decisions too (deny/challenge/terminate always audited).
    # Default OFF: one audit INSERT per business request is real write
    # amplification, and the non-allow decisions — the ones with evidentiary
    # value — are always recorded regardless of this switch.
    ABAC_AUDIT_ALLOW_DECISIONS: bool = False

    # Continuous session risk scoring (issue #67). Behavioural anomalies
    # add their score to the session; crossing the challenge threshold
    # forces a step-up (login → OTP mints a fresh session), crossing the
    # terminate threshold kills the session outright. Scores decay with
    # time (see RISK_DECAY_PER_HOUR) so benign noise accumulated over a
    # long-lived session cannot eventually challenge a legitimate user.
    RISK_CHALLENGE_THRESHOLD: int = Field(default=60, ge=1, le=10000)
    RISK_TERMINATE_THRESHOLD: int = Field(default=90, ge=1, le=10000)
    # Impossible travel: implied speed between consecutive geolocated
    # requests above this many km/h is an anomaly (900 ≈ airliner cruise).
    RISK_IMPOSSIBLE_TRAVEL_KMH: int = Field(default=900, ge=100, le=10000)
    RISK_SCORE_IMPOSSIBLE_TRAVEL: int = Field(default=70, ge=0, le=10000)
    RISK_SCORE_DEVICE_CHANGE: int = Field(default=25, ge=0, le=10000)
    RISK_SCORE_VOLUME_ANOMALY: int = Field(default=30, ge=0, le=10000)
    RISK_SCORE_PRIVILEGE_ESCALATION: int = Field(default=25, ge=0, le=10000)
    # Data-access volume ceiling: requests per rolling window per session.
    # Counted in the shared RateLimitStore (Redis in production), NOT on the
    # session row: a Postgres counter is rolled back by any failing request,
    # which would let an attacker reset their own window for free by probing
    # endpoints that error.
    RISK_VOLUME_WINDOW_SECONDS: int = Field(default=60, ge=5, le=3600)
    RISK_VOLUME_MAX_REQUESTS: int = Field(default=300, ge=1, le=100000)
    # Points shed per hour since the last anomaly. Without decay, benign
    # noise (a browser auto-update +25, one busy minute +30, a stray 403
    # +25) accumulates past the challenge threshold on any long-lived
    # session. Decay applies ONLY to the score: a session already flipped to
    # challenge_required or terminated is never restored in place.
    RISK_DECAY_PER_HOUR: int = Field(default=10, ge=0, le=10000)
    # Session lifetimes. Exceeding either terminates the session with its
    # own audited reason, so an expiry is never mistaken for a risk kill.
    SESSION_MAX_AGE_HOURS: int = Field(default=24, ge=1, le=8760)
    SESSION_IDLE_TIMEOUT_MINUTES: int = Field(default=720, ge=5, le=43200)

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
