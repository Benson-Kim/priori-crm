"""CI-enforced platform ⟂ tenant isolation contract (#71, ADR-0011/0013).

Route-table-driven: every expectation is DERIVED from ``app.routes`` and
each route's dependency graph at test time — no hardcoded path lists — so
a future router that forgets its gate FAILS here instead of shipping.

Legs:

1. **Structural**: every ``/api/v1/platform`` route must carry a
   ``require_role`` gate of exactly ``{PLATFORM_OPERATOR}``. An ungated
   (or wrongly gated) platform route is a contract violation before a
   single request is made.
2. **Tenant → platform (behavioural)**: ADMIN, MANAGER and MEMBER each
   receive 403 from every platform route.
3. **Operator → tenant (behavioural)**: PLATFORM_OPERATOR receives 403
   from every route carrying a tenant role gate (any ``require_role`` /
   ``require_privileged`` whose allowed set excludes the operator).
4. **Static**: no route outside ``/platform`` references
   PLATFORM_OPERATOR — neither in its dependency graph (no role gate may
   accept the operator) nor in its router's source module.
5. **Operator → entire tenant surface (behavioural)**: PLATFORM_OPERATOR
   receives 403 from EVERY /api/v1 route outside ``/platform``, ``/auth``
   and ``/health`` — role-gated or not. Leg 3 only covers gated routes; a
   route with no gate accepts everyone, which is exactly the hole this
   leg closes (review finding B1 on !77). Enforcement point:
   ``get_current_user`` (app/common/dependencies.py) rejects operator
   tokens centrally off the operator surface.
"""

import inspect
import uuid

from fastapi.routing import APIRoute

from app.common.security import create_access_token, hash_password
from app.constants.enums import PRIVILEGED_ROLES, UserRole
from app.main import app
from app.modules.auth.models import User

PLATFORM_PREFIX = "/api/v1/platform"

# Fillers for non-UUID path params so a behavioural call reaches the role
# gate instead of failing path coercion. Anything unknown becomes a UUID.
_PATH_PARAM_FILLERS = {
    "module_key": "customers",
    "currency": "USD",
    "card": "purchase-orders",
    "position": "1",
    "invoice_number": "INV-1",
    "quote_number": "Q-1",
    "po_number": "PO-1",
    "expense_number": "EXP-1",
}


def _seed_user(db, email: str, role: UserRole) -> User:
    user = User(
        email=email,
        password_hash=hash_password("Sup3r!Secret"),
        first_name="Test",
        last_name="User",
        role=role.value,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _auth(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(subject=str(user.id))}"}


def _api_routes() -> list[APIRoute]:
    return [
        r
        for r in app.routes
        if isinstance(r, APIRoute) and r.path.startswith("/api/v1")
    ]


def _role_gates(route: APIRoute) -> list[frozenset[UserRole]]:
    """Extract every role gate from a route's dependency graph.

    Walks the full dependant tree and recognises the two gate factories by
    the qualname of their ``_check`` closures:

    - ``require_role(*roles)`` — the allowed set is read out of the
      closure cell, so the assertion follows the code, not a copy of it;
    - ``require_privileged()`` — equivalent to the PRIVILEGED_ROLES set.
    """
    gates: list[frozenset[UserRole]] = []
    stack = list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        stack.extend(dep.dependencies)
        call = dep.call
        if call is None:
            continue
        qualname = getattr(call, "__qualname__", "")
        if qualname.startswith("require_role."):
            for cell in call.__closure__ or ():
                value = cell.cell_contents
                if (
                    isinstance(value, set)
                    and value
                    and all(isinstance(item, UserRole) for item in value)
                ):
                    gates.append(frozenset(value))
        elif qualname.startswith("require_privileged."):
            gates.append(frozenset(PRIVILEGED_ROLES))
    return gates


def _platform_routes() -> list[APIRoute]:
    return [r for r in _api_routes() if r.path.startswith(PLATFORM_PREFIX)]


def _tenant_gated_routes() -> list[APIRoute]:
    """Routes carrying at least one role gate that excludes the operator."""
    routes = []
    for route in _api_routes():
        if route.path.startswith(PLATFORM_PREFIX):
            continue
        if any(UserRole.PLATFORM_OPERATOR not in gate for gate in _role_gates(route)):
            routes.append(route)
    return routes


#: The operator-accessible surface (mirrors _OPERATOR_SURFACE_PREFIXES in
#: app/common/dependencies.py — deliberately a copy: the test must fail,
#: not follow, if the code's allow-list is silently widened).
_OPERATOR_SURFACE_PREFIXES = ("/api/v1/platform", "/api/v1/auth", "/api/v1/health")

#: The health router hosts the unauthenticated / internal-secret
#: infrastructure endpoints whose paths do NOT share the /health prefix
#: (/ping, /taxes, /internal/email-outbox/*). They are exempted by their
#: endpoint's MODULE, not by path, so the exemption can never swallow a
#: future business route registered elsewhere.
_INFRA_MODULE = "app.modules.health.router"


def _on_operator_surface(route: APIRoute) -> bool:
    return any(
        route.path == p or route.path.startswith(f"{p}/")
        for p in _OPERATOR_SURFACE_PREFIXES
    )


def _tenant_surface_routes() -> list[APIRoute]:
    """Every /api/v1 route the operator must be rejected from (leg 5)."""
    routes = []
    for route in _api_routes():
        if _on_operator_surface(route):
            continue
        module = inspect.getmodule(route.endpoint)
        if module is not None and module.__name__ == _INFRA_MODULE:
            continue
        routes.append(route)
    return routes


def _fill_path(route: APIRoute) -> str:
    path = route.path
    for param in route.dependant.path_params:
        filler = _PATH_PARAM_FILLERS.get(param.name, str(uuid.uuid4()))
        path = path.replace("{" + param.name + "}", filler)
    return path


def _call(client, route: APIRoute, method: str, headers: dict):
    """One behavioural request; body-taking methods send an empty object.

    Role-gate dependencies are solved (and raise) before request-body
    validation, so a 403 from the gate always beats a 422 from the body.
    """
    url = _fill_path(route)
    if method in ("POST", "PUT", "PATCH"):
        return client.request(method, url, headers=headers, json={})
    return client.request(method, url, headers=headers)


class TestStructuralContract:
    def test_route_table_is_derived_not_empty(self):
        """Sanity: the derivation itself must never silently go empty."""
        assert len(_platform_routes()) >= 4
        assert len(_tenant_gated_routes()) >= 4

    def test_every_platform_route_gated_on_exactly_platform_operator(self):
        """A future /platform route without the operator gate FAILS here."""
        for route in _platform_routes():
            gates = _role_gates(route)
            assert gates, f"{route.path} carries NO role gate"
            assert frozenset({UserRole.PLATFORM_OPERATOR}) in gates, (
                f"{route.path} is not gated on exactly PLATFORM_OPERATOR: {gates}"
            )

    def test_no_route_outside_platform_accepts_the_operator(self):
        """Static leg (dependency graph): the operator role must never
        satisfy a gate anywhere outside /platform."""
        for route in _api_routes():
            if route.path.startswith(PLATFORM_PREFIX):
                continue
            for gate in _role_gates(route):
                assert UserRole.PLATFORM_OPERATOR not in gate, (
                    f"{route.path} accepts PLATFORM_OPERATOR via {gate}"
                )

    def test_no_router_module_outside_platform_names_the_operator(self):
        """Static leg (source): no non-platform route's endpoint module may
        even reference PLATFORM_OPERATOR."""
        import inspect

        for route in _api_routes():
            if route.path.startswith(PLATFORM_PREFIX):
                continue
            module = inspect.getmodule(route.endpoint)
            if module is None or module.__name__ == "app.modules.platform.router":
                continue
            source = inspect.getsource(module)
            assert "PLATFORM_OPERATOR" not in source, (
                f"{module.__name__} (serving {route.path}) references "
                "PLATFORM_OPERATOR outside /platform"
            )


class TestTenantRolesRejectedFromPlatform:
    def test_admin_manager_member_403_on_every_platform_route(self, client, db):
        users = {
            role: _seed_user(db, f"{role.value}@mail.com", role)
            for role in (UserRole.ADMIN, UserRole.MANAGER, UserRole.MEMBER)
        }
        for route in _platform_routes():
            for method in route.methods - {"HEAD", "OPTIONS"}:
                for role, user in users.items():
                    resp = _call(client, route, method, _auth(user))
                    assert resp.status_code == 403, (
                        f"{role.value} got {resp.status_code} "
                        f"from {method} {route.path}"
                    )


class TestOperatorRejectedFromTenantGates:
    def test_operator_403_on_every_tenant_role_gated_route(self, client, db):
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        headers = _auth(operator)
        for route in _tenant_gated_routes():
            for method in route.methods - {"HEAD", "OPTIONS"}:
                resp = _call(client, route, method, headers)
                assert resp.status_code == 403, (
                    f"operator got {resp.status_code} from {method} {route.path}"
                )


class TestOperatorRejectedFromEntireTenantSurface:
    """Leg 5 — closes review finding B1 on !77.

    Before the central rejection in ``get_current_user``, every route
    without a role gate (the majority: customers, reports, sales-desk,
    statements, dashboard, …) served an operator token tenant data with
    200. This leg fails in that world by construction and pins the fix:
    403 from EVERY tenant-surface route, gated or not.
    """

    def test_tenant_surface_derivation_is_not_empty(self):
        """Sanity: the derivation must cover the real business surface."""
        assert len(_tenant_surface_routes()) >= 50

    def test_operator_403_on_every_route_outside_platform_auth_health(
        self, client, db
    ):
        operator = _seed_user(db, "operator@mail.com", UserRole.PLATFORM_OPERATOR)
        headers = _auth(operator)
        for route in _tenant_surface_routes():
            for method in route.methods - {"HEAD", "OPTIONS"}:
                resp = _call(client, route, method, headers)
                assert resp.status_code == 403, (
                    f"operator got {resp.status_code} from {method} {route.path} "
                    "(tenant surface must reject operator tokens)"
                )
