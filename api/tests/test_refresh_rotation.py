"""Refresh-token rotation closes the concurrent reuse window (issue #44).

The "spend" of a presented refresh-token jti is a single atomic
revoke-and-report (TokenDenylist.revoke_if_new): exactly one concurrent
presenter can rotate; every other presenter takes the reuse-detected path
(family fence + 401) and never mints a descendant chain.
"""

import threading
import uuid

import pytest

from app.common.exceptions import UnauthorizedException
from app.common.security import create_refresh_token, hash_password
from app.constants.enums import SessionStatus
from app.modules.auth.models import User, UserSession
from app.modules.auth.service import AuthService, _refresh_token_denylist
from tests.conftest import USING_POSTGRES, TestingSessionLocal


def _seed_user(db) -> User:
    user = User(
        email=f"rotate-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Securepass123!"),
        first_name="Rota",
        last_name="Tion",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _session_refresh_token(db, user: User) -> str:
    """A refresh token carrying a real session's sid (#67 review F7).

    Sessionless (legacy) refresh tokens are now refused outright, so the
    rotation-mechanics tests mint the shape every post-#67 login produces.
    """
    session = UserSession(user_id=user.id, status=SessionStatus.ACTIVE.value)
    db.add(session)
    db.commit()
    token, _, _ = create_refresh_token(
        subject=str(user.id), extra={"sid": str(session.id)}
    )
    return token


class TestSequentialReuseDetection:
    """Deterministic reuse path: spent token -> family fence -> 401."""

    def test_reuse_fences_family_and_kills_descendants(self, db):
        user = _seed_user(db)
        svc = AuthService(db)
        token_a = _session_refresh_token(db, user)

        # First presentation wins the rotation and mints a descendant.
        _access, token_b = svc.refresh_access_token(token_a)

        # Re-presenting the spent token is theft evidence: 401 + family fence.
        with pytest.raises(UnauthorizedException):
            svc.refresh_access_token(token_a)
        assert _refresh_token_denylist().get_fence(f"user:{user.id}") is not None

        # The winner's descendant chain dies on the fence too.
        with pytest.raises(UnauthorizedException):
            svc.refresh_access_token(token_b)

    def test_logout_is_idempotent_and_spends_the_token(self, db):
        user = _seed_user(db)
        svc = AuthService(db)
        token = _session_refresh_token(db, user)

        svc.logout(token)  # spends the jti; boolean result ignored
        svc.logout(token)  # second logout is a no-op, never errors

        with pytest.raises(UnauthorizedException):
            svc.refresh_access_token(token)


@pytest.mark.skipif(
    not USING_POSTGRES,
    reason="Concurrent sessions need real per-thread connections",
)
class TestConcurrentRotation:
    """Two simultaneous refreshes with one token: exactly one succeeds."""

    def test_concurrent_refresh_single_winner_and_fence(self, db):
        user = _seed_user(db)
        token = _session_refresh_token(db, user)

        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        descendants: list[str] = []

        def worker() -> None:
            session = TestingSessionLocal()
            try:
                svc = AuthService(session)
                barrier.wait()
                try:
                    _access, new_refresh = svc.refresh_access_token(token)
                    outcomes.append("rotated")
                    descendants.append(new_refresh)
                except UnauthorizedException:
                    outcomes.append("rejected")
            finally:
                session.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        # Exactly one presenter rotates; the other takes the reuse path,
        # so a second valid descendant chain is never minted.
        assert sorted(outcomes) == ["rejected", "rotated"]
        assert len(descendants) == 1

        # The loser wrote the family fence ...
        assert _refresh_token_denylist().get_fence(f"user:{user.id}") is not None
        # ... so the winner's descendant is rejected as well.
        with pytest.raises(UnauthorizedException):
            AuthService(db).refresh_access_token(descendants[0])
