"""GET /platform/owners hardening (#71, ADR-0013 Phase A).

Pins the hardened listing contract:

- backward-compatible: the ``owners`` key keeps its shape; each entry
  additively gains ``status`` and ``disabledModuleCount``; pagination
  ``metadata`` is additive and optional;
- stable name-ordered pagination with the shared window semantics;
- LIKE-safe name search (wildcards in the term match literally);
- per-owner summary is bulk-counted (correct counts per owner, zero for
  owners with no explicit disable).
"""

import uuid

from app.common.security import hash_password
from app.constants.enums import UserRole
from app.modules.auth.models import User
from app.modules.owner.models import OwnerProfile
from app.modules.owner.service import OwnerService
from tests.operator_mfa_utils import auth_headers

OWNERS_URL = "/api/v1/platform/owners"


def _seed_operator(db) -> User:
    user = User(
        email="operator@mail.com",
        password_hash=hash_password("Sup3r!Secret"),
        first_name="Test",
        last_name="User",
        role=UserRole.PLATFORM_OPERATOR.value,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _auth(user: User) -> dict:
    # Operators get a full-MFA token + fresh step-up code (ADR-0014, #73).
    return auth_headers(user)


def _seed_owners(db, names: list[str]) -> dict[str, uuid.UUID]:
    """First name becomes the singleton profile; the rest are extra rows."""
    ids: dict[str, uuid.UUID] = {}
    singleton = OwnerService(db).get_or_create()
    singleton.full_name = names[0]
    ids[names[0]] = singleton.id
    for name in names[1:]:
        profile = OwnerProfile(id=uuid.uuid4(), full_name=name)
        db.add(profile)
        ids[name] = profile.id
    db.commit()
    return ids


class TestBackwardCompatibleShape:
    def test_no_params_returns_every_owner_with_summary(self, client, db):
        operator = _seed_operator(db)
        _seed_owners(db, ["Acme Ltd", "Beta Corp"])
        resp = client.get(OWNERS_URL, headers=_auth(operator))
        assert resp.status_code == 200
        body = resp.json()
        assert [o["fullName"] for o in body["owners"]] == ["Acme Ltd", "Beta Corp"]
        for owner in body["owners"]:
            assert owner["status"] == "active"
            assert owner["disabledModuleCount"] == 0
        # Additive metadata is present and truthful.
        assert body["metadata"]["has_next"] is False


class TestSummary:
    def test_disabled_module_count_and_status_per_owner(self, client, db):
        operator = _seed_operator(db)
        ids = _seed_owners(db, ["Acme Ltd", "Beta Corp"])
        # Recompute headers per PATCH: each destructive call needs a
        # FRESH step-up code (replay fence, ADR-0014).
        for key in ("customers", "reports"):
            resp = client.patch(
                f"{OWNERS_URL}/{ids['Acme Ltd']}/modules/{key}",
                json={"enabled": False},
                headers=_auth(operator),
            )
            assert resp.status_code == 200
        # An explicit enabled=true override must NOT count as disabled.
        resp = client.patch(
            f"{OWNERS_URL}/{ids['Beta Corp']}/modules/vendors",
            json={"enabled": True},
            headers=_auth(operator),
        )
        assert resp.status_code == 200
        resp = client.patch(
            f"{OWNERS_URL}/{ids['Beta Corp']}/status",
            json={"status": "suspended"},
            headers=_auth(operator),
        )
        assert resp.status_code == 200

        owners = {
            o["fullName"]: o
            for o in client.get(OWNERS_URL, headers=_auth(operator)).json()["owners"]
        }
        assert owners["Acme Ltd"]["disabledModuleCount"] == 2
        assert owners["Acme Ltd"]["status"] == "active"
        assert owners["Beta Corp"]["disabledModuleCount"] == 0
        assert owners["Beta Corp"]["status"] == "suspended"


class TestSearchAndPagination:
    def test_name_search_is_case_insensitive_substring(self, client, db):
        operator = _seed_operator(db)
        _seed_owners(db, ["Acme Ltd", "Beta Corp", "Acme West"])
        resp = client.get(
            OWNERS_URL, params={"search": "acme"}, headers=_auth(operator)
        )
        assert [o["fullName"] for o in resp.json()["owners"]] == [
            "Acme Ltd",
            "Acme West",
        ]

    def test_search_wildcards_match_literally(self, client, db):
        operator = _seed_operator(db)
        _seed_owners(db, ["100% Cotton Co", "Percenters"])
        resp = client.get(OWNERS_URL, params={"search": "%"}, headers=_auth(operator))
        assert [o["fullName"] for o in resp.json()["owners"]] == ["100% Cotton Co"]

    def test_pagination_window_and_total(self, client, db):
        operator = _seed_operator(db)
        _seed_owners(db, ["Acme Ltd", "Beta Corp", "Gamma Inc"])
        resp = client.get(
            OWNERS_URL,
            params={"per_page": 2, "withTotal": True},
            headers=_auth(operator),
        )
        body = resp.json()
        assert [o["fullName"] for o in body["owners"]] == ["Acme Ltd", "Beta Corp"]
        assert body["metadata"]["total"] == 3
        assert body["metadata"]["has_next"] is True

        resp = client.get(
            OWNERS_URL,
            params={"per_page": 2, "page": 2},
            headers=_auth(operator),
        )
        body = resp.json()
        assert [o["fullName"] for o in body["owners"]] == ["Gamma Inc"]
        assert body["metadata"]["has_next"] is False
        assert body["metadata"]["has_prev"] is True

    def test_page_beyond_bound_is_422_not_500(self, client, db):
        """!77 review (minor): the Query params must mirror
        PaginationParams' le=1000, otherwise page=1001 raises a pydantic
        ValidationError INSIDE the handler and surfaces as a 500."""
        operator = _seed_operator(db)
        for url in (OWNERS_URL, "/api/v1/platform/audit"):
            resp = client.get(url, params={"page": 1001}, headers=_auth(operator))
            assert resp.status_code == 422, url
