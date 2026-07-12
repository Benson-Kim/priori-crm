from app.modules.invoices.router import router as invoice_router


def _dependency_callables(route) -> set:
    found: set = set()
    stack = [route.dependant]
    while stack:
        dep = stack.pop()
        if dep.call is not None:
            found.add(dep.call)
        stack.extend(dep.dependencies)
    return found


def _has_privileged_gate(route) -> bool:
    return any(
        getattr(call, "__qualname__", "").startswith("require_privileged")
        for call in _dependency_callables(route)
    )


def test_delete_invoice_route_requires_privileged() -> None:
    route = next(
        r
        for r in invoice_router.routes
        if getattr(r, "path", None) == "/{invoice_id}"
        and "DELETE" in getattr(r, "methods", set())
    )

    assert _has_privileged_gate(route)
