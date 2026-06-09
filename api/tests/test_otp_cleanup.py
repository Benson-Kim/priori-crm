"""W3.7 - OTP retention cleanup (AUTH-DBA-2)."""

from datetime import UTC, datetime, timedelta

from app.common.security import hash_password
from app.modules.auth.models import OTPCode, User
from app.modules.auth.service import AuthService


def _make_user(db) -> User:
    user = User(
        email="purge@example.com",
        password_hash=hash_password("password123"),
        first_name="Purge",
        last_name="Test",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_purge_removes_used_and_expired_keeps_active(db):
    user = _make_user(db)
    now = datetime.now(UTC)

    # Old + used -> purged
    db.add(
        OTPCode(
            user_id=user.id,
            code="111111",
            is_used=True,
            expires_at=now - timedelta(minutes=30),
            created_at=now - timedelta(minutes=30),
        )
    )
    # Old + expired (unused) -> purged
    db.add(
        OTPCode(
            user_id=user.id,
            code="222222",
            is_used=False,
            expires_at=now - timedelta(minutes=20),
            created_at=now - timedelta(minutes=20),
        )
    )
    # Fresh + active -> kept
    db.add(
        OTPCode(
            user_id=user.id,
            code="333333",
            is_used=False,
            expires_at=now + timedelta(minutes=5),
            created_at=now,
        )
    )
    db.commit()

    deleted = AuthService(db).purge_expired_otps()
    assert deleted == 2

    remaining = db.query(OTPCode).filter(OTPCode.user_id == user.id).all()
    assert len(remaining) == 1
    assert remaining[0].code == "333333"
