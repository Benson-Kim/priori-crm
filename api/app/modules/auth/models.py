import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.database import Base
from app.constants.enums import SessionStatus, UserRole


class User(Base):
    """Application user account."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=UserRole.MEMBER,
        server_default=UserRole.MEMBER.value,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    otp_codes: Mapped[list["OTPCode"]] = relationship(
        "OTPCode", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User {self.email}>"


class OTPCode(Base):
    """One-time password codes for two-factor authentication."""

    __tablename__ = "otp_codes"

    __table_args__ = (
        # Hot path: verify_otp filters (user_id, is_used) ordered by
        # created_at desc, and _invalidate_pending_otps filters
        # (user_id, is_used). A composite index on these columns avoids a
        # scan of a user's OTP history on every login/verify.
        Index(
            "ix_otp_user_unused_created",
            "user_id",
            "is_used",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="SHA-256 digest of the OTP code (never plaintext)",
    )
    is_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    user: Mapped["User"] = relationship("User", back_populates="otp_codes")

    @property
    def is_expired(self) -> bool:
        """Check if this OTP code has expired."""
        return datetime.now(UTC) > self.expires_at

    @property
    def is_valid(self) -> bool:
        """Check if this OTP code is still usable."""
        return not self.is_used and not self.is_expired

    def __repr__(self) -> str:
        return f"<OTPCode user_id={self.user_id} used={self.is_used}>"


class UserSession(Base):
    """One authenticated session with its continuous risk state (issue #67).

    Minted by ``verify_otp`` (its id travels as the ``sid`` claim in both
    the access and refresh tokens) and re-scored on EVERY request by the
    zero-trust gate: behavioural signals (impossible travel, data-access
    volume, device change, privilege-escalation attempts) raise
    ``risk_score``; crossing thresholds flips ``status`` to
    ``challenge_required`` (step-up via the existing login → OTP flow) or
    ``terminated`` (dead forever). A non-active session is never cleared
    in place — re-authentication mints a NEW session row.

    ``risk_score`` decays from ``risk_updated_at`` at ``RISK_DECAY_PER_HOUR``
    so accumulated benign noise cannot eventually challenge a legitimate
    long-lived session; the decay is computed on read and settled into the
    column only when a detector fires, so a quiet session costs no writes.
    The volume-window counter deliberately lives in the shared
    ``RateLimitStore``, not here: a column would be rolled back by any
    failing request, letting an attacker reset their window by probing
    endpoints that error.
    """

    __tablename__ = "user_sessions"

    __table_args__ = (
        CheckConstraint(
            f"status IN {SessionStatus.db_check_values()}",
            name="valid_status",
        ),
        # Hot path: the gate loads by PK; ops queries list a user's live
        # sessions ("terminate everything for this account").
        Index("ix_user_sessions_user_status", "user_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=SessionStatus.ACTIVE,
        server_default=SessionStatus.ACTIVE.value,
    )
    risk_score: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    risk_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When risk_score was last raised; decay accrues from here",
    )
    device_fingerprint: Mapped[str | None] = mapped_column(
        String(160),
        nullable=True,
        comment="Last presented device fingerprint (client: or derived: form)",
    )
    last_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    last_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_geo_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When last_lat/last_lon were captured (impossible-travel anchor)",
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    escalation_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
        comment="Privilege-escalation attempts (RBAC 403s) on this session",
    )
    termination_reason: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    user: Mapped["User"] = relationship("User")

    def __repr__(self) -> str:
        return (
            f"<UserSession {self.id} user={self.user_id} "
            f"status={self.status} risk={self.risk_score}>"
        )


class UserBaseline(Base):
    """Per-user behavioural baseline for graduated risk scoring (issue #67).

    Records what is NORMAL for one user — known devices, typical countries,
    typical active hours, and typical per-window request volume per
    data-sensitivity class — so the risk detectors can weigh *deviation
    from this user's own behaviour* instead of firing on absolutes. A new
    device or country is a SOFT signal only when the user has an
    established baseline that it deviates from.

    Trust enters this table exclusively through a passed OTP step-up
    (``AuthService.verify_otp`` absorbs the verifying request's context):
    an attacker holding a stolen password or token has no inbox and so can
    never launder their device or location into the victim's baseline.
    Hour and volume statistics, by contrast, learn continuously from
    scored requests — they exist to suppress false positives, not to grant
    trust, so poisoning them buys an attacker nothing they did not already
    have.
    """

    __tablename__ = "user_behavior_baselines"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    known_devices: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        comment="Device fingerprints trusted via a passed OTP step-up",
    )
    known_countries: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        comment="ISO country codes trusted via a passed OTP step-up",
    )
    hour_counts: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        comment="Requests observed per tenant-local hour ('0'..'23')",
    )
    hour_observations: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="Total observations behind hour_counts (min-sample gate)",
    )
    volume_baselines: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        comment=(
            "Per sensitivity class: EWMA of requests per volume window "
            "{class: {ewma, count, windows, window_started_at}}"
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship("User")

    def __repr__(self) -> str:
        return (
            f"<UserBaseline user={self.user_id} "
            f"devices={len(self.known_devices or [])} "
            f"countries={len(self.known_countries or [])}>"
        )


class PasswordResetToken(Base):
    """Single-use, expiring token backing the forgot/reset-password flow.

    Security posture mirrors OTPCode: only the SHA-256 digest of the token is
    persisted (the plaintext exists only in the emailed reset link), the token
    is single-use (``is_used``), expiring (``expires_at``) and attempt-bounded
    (``attempt_count``). A DB read can never yield a usable reset token.
    """

    __tablename__ = "password_reset_tokens"

    __table_args__ = (
        # Hot path: look up a user's latest live token ordered by created_at.
        Index(
            "ix_password_reset_user_unused_created",
            "user_id",
            "is_used",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    token: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="SHA-256 digest of the reset token (never plaintext)",
    )
    is_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    user: Mapped["User"] = relationship("User")

    @property
    def is_expired(self) -> bool:
        """Check if this reset token has expired."""
        return datetime.now(UTC) > self.expires_at

    @property
    def is_valid(self) -> bool:
        """Check if this reset token is still usable."""
        return not self.is_used and not self.is_expired

    def __repr__(self) -> str:
        return f"<PasswordResetToken user_id={self.user_id} used={self.is_used}>"
