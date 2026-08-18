"""CI-enforced module-gate structural contract (#71, !77 review §2).

The entitlement/suspension module gate (``require_module``) is attached
per-router at ``include_router`` time (api/app/main.py). Coverage was
complete but enforced nowhere: a future business router registered
without ``_module_gate`` would silently bypass BOTH entitlements and the
module-gate suspension seam. This suite turns that convention into a
route-table-driven contract, mirroring the closure-introspection
technique of ``_role_gates`` in test_platform_isolation_contract.py —
the assertion follows the code, not a copy of it.

Legs:

1. **Coverage**: every /api/v1 route outside the essential surfaces
   (auth, owner, health, dashboard), /platform, and the health router's
   infrastructure endpoints must carry a ``require_module`` dependency
   for a non-essential ModuleKey.
2. **Inverse**: essential-surface and /platform routes must carry NO
   module gate (essential modules are never gated — defense in depth,
   and platform administration must keep working when every toggleable
   module is disabled).
"""

import inspect

import pytest
from fastapi.routing import APIRoute

from app.constants.enums import ESSENTIAL_MODULES, ModuleKey
from app.main import app

#: Surfaces that are deliberately not module-gated: essential modules
#: (auth, owner, health, dashboard — never disableable) and the platform
#: console (must keep working with every toggleable module disabled).
_UNGATED_PREFIXES = (
    "/api/v1/auth",
    "/api/v1/owner",
    "/api/v1/health",
    "/api/v1/dashboard",
    "/api/v1/platform",
)

#: The health router also hosts infrastructure endpoints whose paths do
#: not share the /health prefix (/ping, /taxes, /internal/email-outbox/*).
#: Exempted by their endpoint's MODULE, not by path, so the exemption can
#: never swallow a future business route registered elsewhere.
_INFRA_MODULE = "app.modules.health.router"


def _api_routes() -> list[APIRoute]:
    return [
        r
        for r in app.routes
        if isinstance(r, APIRoute) and r.path.startswith("/api/v1")
    ]


def _is_ungated_surface(route: APIRoute) -> bool:
    if any(
        route.path == p or route.path.startswith(f"{p}/") for p in _UNGATED_PREFIXES
    ):
        return True
    module = inspect.getmodule(route.endpoint)
    return module is not None and module.__name__ == _INFRA_MODULE


def _module_gates(route: APIRoute) -> list[ModuleKey]:
    """Extract every ``require_module`` gate from a route's dependency graph.

    Walks the full dependant tree and recognises the gate factory by the
    qualname of its ``_check`` closure; the gated ModuleKey is read out of
    the closure cell, so the assertion follows the code, not a copy of it.
    """
    gates: list[ModuleKey] = []
    stack = list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        stack.extend(dep.dependencies)
        call = dep.call
        if call is None:
            continue
        qualname = getattr(call, "__qualname__", "")
        if qualname.startswith("require_module."):
            for cell in call.__closure__ or ():
                if isinstance(cell.cell_contents, ModuleKey):
                    gates.append(cell.cell_contents)
    return gates


def _business_routes() -> list[APIRoute]:
    return [r for r in _api_routes() if not _is_ungated_surface(r)]


@pytest.mark.no_db
class TestModuleGateContract:
    def test_route_table_derivation_not_empty(self):
        """Sanity: the derivations themselves must never silently go empty."""
        assert len(_business_routes()) >= 50
        assert len(_api_routes()) > len(_business_routes())

    def test_every_business_route_carries_a_module_gate(self):
        """A future business router registered without _module_gate FAILS
        here instead of silently bypassing entitlements and suspension."""
        for route in _business_routes():
            gates = _module_gates(route)
            assert gates, (
                f"{route.path} carries NO require_module gate: a router "
                "outside auth/owner/health/dashboard/platform must be "
                "module-gated (entitlements + suspension, ADR-0011/0013)"
            )
            for key in gates:
                assert key not in ESSENTIAL_MODULES, (
                    f"{route.path} is gated on essential module "
                    f"'{key.value}' — essential modules are never gated"
                )

    def test_no_module_gate_on_essential_or_platform_surfaces(self):
        """Essential modules are never gated; the platform console must
        keep working when every toggleable module is disabled."""
        for route in _api_routes():
            if not _is_ungated_surface(route):
                continue
            gates = _module_gates(route)
            assert not gates, (
                f"{route.path} unexpectedly carries a module gate: "
                f"{[k.value for k in gates]}"
            )
