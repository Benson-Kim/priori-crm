"""PO-16 security review — endpoint AuthN/AuthZ matrix + data-exposure (#17).

These tests pin the security contract of the Purchase Orders module so it
cannot silently regress, mapping the PO-16 acceptance gates to automated
checks:

- AuthN: every PO route is authenticated. The PO router injects
  ``PurchaseOrderServiceDep``, which depends on ``CurrentUser`` ->
  ``get_current_user``; a route exposing none of the auth-bearing
  dependencies would be an unauthenticated hole. We walk the router's route
  table and assert each route transitively resolves the current-user
  dependency.
- AuthZ: the mutating/destructive financial actions (Send, Mark-as-sent,
  Record-payment, Delete PO, Delete document) are gated by
  ``require_privileged``; read/preview routes are not over-gated.
- Data exposure: ``storage_key`` is never a field on any PO response schema
  (defence in depth on top of the per-schema exclusions).

The checks are pure introspection (no DB), so they run identically on
SQLite and PostgreSQL.
"""

from __future__ import annotations

from app.common.dependencies import get_current_user
from app.modules.purchase_orders import router as po_router_module
from app.modules.purchase_orders.router import router as po_router
from app.modules.purchase_orders.schemas import (
    PurchaseOrderDocumentResponse,
    PurchaseOrderPaymentResponse,
    PurchaseOrderResponse,
)

# Routes (method, path-suffix) that MUST be gated by require_privileged.
# Paths are matched by suffix against the router-local path (no API prefix).
_PRIVILEGED_ROUTES = {
    ("POST", "/{po_id}/send"),
    ("POST", "/{po_id}/mark-as-sent"),
    ("POST", "/{po_id}/payments"),
    ("DELETE", "/{po_id}"),
    ("DELETE", "/{po_id}/documents/{document_id}"),
}


def _dependency_callables(route) -> set:
    """All dependency callables resolved for a route (params + decorator deps).

    Flattens the FastAPI dependant tree so a transitively-required dependency
    (e.g. get_current_user reached via the service dependency) is detected
    regardless of nesting depth.
    """
    found: set = set()
    stack = [route.dependant]
    while stack:
        dep = stack.pop()
        if dep.call is not None:
            found.add(dep.call)
        stack.extend(dep.dependencies)
    return found


def _po_api_routes() -> list:
    """Concrete API routes on the PO router (skip mounts without methods)."""
    return [r for r in po_router.routes if getattr(r, "methods", None)]


class TestEveryRouteAuthenticated:
    def test_all_routes_require_current_user(self) -> None:
        """No PO route is reachable without an authenticated user."""
        offenders = []
        for route in _po_api_routes():
            if get_current_user not in _dependency_callables(route):
                offenders.append((sorted(route.methods), route.path))
        assert offenders == [], (
            "PO routes missing authentication (no get_current_user in the "
            f"dependency tree): {offenders}"
        )


class TestPrivilegedActionsGated:
    def test_destructive_and_financial_routes_require_privileged(self) -> None:
        """Send / Mark-as-sent / Payments / Delete are role-gated."""
        from app.common.dependencies import require_privileged

        # require_privileged() returns a fresh closure per call, so identity
        # comparison is unreliable; match by the dependency's qualified name.
        def _has_privileged_gate(route) -> bool:
            for call in _dependency_callables(route):
                qualname = getattr(call, "__qualname__", "")
                if qualname.startswith("require_privileged"):
                    return True
            return False

        # Sanity: the dependency factory exists and is importable.
        assert callable(require_privileged)

        seen = set()
        for route in _po_api_routes():
            for method in route.methods:
                key = (method, route.path)
                if key in _PRIVILEGED_ROUTES:
                    seen.add(key)
                    assert _has_privileged_gate(route), (
                        f"{method} {route.path} must be gated by "
                        "require_privileged"
                    )

        missing = _PRIVILEGED_ROUTES - seen
        assert not missing, f"expected privileged routes not found on router: {missing}"

    def test_read_and_preview_routes_not_overgated(self) -> None:
        """GET reads and the calculate preview stay authenticated-only."""

        def _has_privileged_gate(route) -> bool:
            return any(
                getattr(c, "__qualname__", "").startswith("require_privileged")
                for c in _dependency_callables(route)
            )

        for route in _po_api_routes():
            if "GET" in route.methods and _has_privileged_gate(route):
                raise AssertionError(
                    f"GET {route.path} should not require a privileged role "
                    "(reads are authenticated-only)."
                )


class TestNoStorageKeyExposure:
    def test_storage_key_absent_from_response_schemas(self) -> None:
        for schema in (
            PurchaseOrderResponse,
            PurchaseOrderDocumentResponse,
            PurchaseOrderPaymentResponse,
        ):
            assert "storage_key" not in schema.model_fields, (
                f"{schema.__name__} must never expose storage_key"
            )

    def test_router_module_imports_require_privileged(self) -> None:
        # The router module must reference the gate it depends on.
        assert hasattr(po_router_module, "require_privileged")
