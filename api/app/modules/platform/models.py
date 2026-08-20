"""Operator MFA persistence (ADR-0014).

One TOTP enrollment row per platform operator plus that operator's
single-use recovery codes. Deliberately keyed on ``users.id`` with no
owner/tenant column: MFA is operator-account state on the platform
authority axis (ADR-0011), not tenant state.

Secret handling: only the Fernet ciphertext of the base32 TOTP seed is
persisted (``app/common/mfa.py`` owns the crypto); recovery codes are
stored as SHA-256 digests. A database read can never yield a usable
second factor.

QA finding 09 note: nothing here creates or promotes accounts — rows are
written only by ``OperatorMfaService`` acting on the already-seeded,
authenticated operator itself.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.database import Base
from app.constants.enums import OperatorMfaStatus


class OperatorMfaTotp(Base):
    """One operator's TOTP enrollment (pending until code-confirmed)."""

    __tablename__ = "operator_mfa_totp"

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
        unique=True,
    )
    secret_encrypted: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment="Fernet ciphertext of the base32 TOTP seed (never plaintext)",
    )
    status: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default=OperatorMfaStatus.PENDING,
        server_default=OperatorMfaStatus.PENDING.value,
        comment="pending (unconfirmed) | active (enforced)",
    )
    last_used_counter: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment=(
            "Highest accepted TOTP time-step counter (replay fence shared "
            "across login and step-up); NULL until first acceptance"
        ),
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    def __repr__(self) -> str:
        return f"<OperatorMfaTotp user_id={self.user_id} status={self.status}>"


class OperatorRecoveryCode(Base):
    """One single-use MFA recovery code (digest only) for one operator."""

    __tablename__ = "operator_mfa_recovery_codes"

    __table_args__ = (
        # Hot path: burn/count a user's unused codes.
        Index("ix_operator_recovery_user_used", "user_id", "used_at"),
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
    code_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="SHA-256 digest of the normalized recovery code",
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    def __repr__(self) -> str:
        return f"<OperatorRecoveryCode user_id={self.user_id} used={self.used_at is not None}>"
