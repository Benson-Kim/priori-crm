"""Application configuration with validation and environment management."""
import secrets
from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables with validation."""

    # API Keys
    MOCK_PAYMENT_GATEWAY_KEY: str = "mock-key-for-dev"
    APP_NAME: str = "Priori Technologies"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: Literal["development", "test", "staging", "production"] = "development"
    DEBUG: bool = Field(default=False)
    
    # API
    API_V1_PREFIX: str = "/api/v1"
    
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
    AWS_REGION: str = "af-south-1"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    SES_SENDER_EMAIL: str = "noreply@example.com"
    SES_MAX_RETRIES: int = Field(default=3, ge=1, le=5)
    
    # CORS
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"
    
    # Rate Limiting
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_PER_MINUTE: int = Field(default=60, ge=10, le=1000)
    # Only trust X-Forwarded-For for client identity when the app sits
    # behind a known, trusted reverse proxy. The header is client-spoofable
    # when exposed directly, so it stays off by default (W-5).
    RATE_LIMIT_TRUST_FORWARDED_FOR: bool = False
    
    # Monitoring
    SENTRY_DSN: str = ""
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    
    # Batch Processing
    BATCH_SIZE: int = Field(default=1000, ge=100, le=10000)
    BATCH_TIMEOUT_SECONDS: int = Field(default=300, ge=60, le=3600)

    # File Storage (P-13)
    # Root directory all uploaded objects are confined to. StorageService
    # resolves every key under this path and refuses anything that escapes it.
    UPLOAD_DIR: str = "uploads"

    # Internal machine-to-machine secret (P-13). Protects internal endpoints
    # such as the nightly overdue-transition job. Empty by default so internal
    # endpoints fail closed (refused) until a secret is explicitly configured.
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

    @model_validator(mode="after")
    def validate_production_hardening(self) -> "Settings":
        """Fail fast on insecure configuration in production (LIB-OPS-2)."""
        if self.ENVIRONMENT != "production":
            return self

        errors: list[str] = []

        if self.DEBUG:
            errors.append("DEBUG must be False in production")

        if not self.AWS_ACCESS_KEY_ID or not self.AWS_SECRET_ACCESS_KEY:
            errors.append("AWS SES credentials are required in production")

        if not self.SES_SENDER_EMAIL or self.SES_SENDER_EMAIL == "noreply@example.com":
            errors.append("SES_SENDER_EMAIL must be configured in production")

        if self.MOCK_PAYMENT_GATEWAY_KEY == "mock-key-for-dev":
            errors.append("MOCK_PAYMENT_GATEWAY_KEY must not use the development default in production")

        if errors:
            raise ValueError(
                "Insecure production configuration: " + "; ".join(errors)
            )

        return self

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse comma-separated CORS origins into list."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

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