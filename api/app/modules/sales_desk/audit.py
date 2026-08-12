"""Append-only, fail-closed access auditing for Sales Desk CSV exports.

Mirrors the reports module's audit contract (issue #45): every export
request appends exactly one audit row using the SAME request-scoped session
— success rows after the response is built, failure rows after rollback —
and a response is never served if its success audit cannot be persisted
(the generated file is removed instead).
"""

import logging
import uuid
from collections.abc import Generator
from pathlib import Path

from fastapi import Request

from app.common.audit import record_audit_event
from app.common.dependencies import CurrentUser, DbSession

logger = logging.getLogger(__name__)

_SALES_DESK_AUDIT_NAMESPACE = uuid.UUID("7f0c8a2e-5d31-4b7a-9c46-2f8e1a6b0d94")

#: Query parameters recorded verbatim in the audit trail (active filters).
_FILTER_KEYS = ("tab", "ownerId", "owner", "hygiene", "showClosed")


def _export_type(path: str) -> str:
    return "dashboard" if path.endswith("/dashboard") else "pipeline"


def _filters(request: Request) -> dict[str, str | bool]:
    filters: dict[str, str | bool] = {
        key: request.query_params[key]
        for key in _FILTER_KEYS
        if key in request.query_params
    }
    if request.query_params.get("search"):
        filters["search_present"] = True
    return filters


def _payload(
    request: Request,
    *,
    export_type: str,
    success: bool,
    failure: str | None,
) -> dict:
    return {
        "request_id": getattr(request.state, "request_id", None),
        "success": success,
        "failure": failure,
        "export_type": export_type,
        "filters": _filters(request),
        "export_metrics": getattr(request.state, "sales_desk_export_metrics", None),
        "deployment_boundary": "single_organization",
    }


def _append_audit(db: DbSession, *, actor_id, export_type: str, payload: dict) -> None:
    record_audit_event(
        db,
        actor_id=actor_id,
        entity_type="sales_desk_export",
        entity_id=uuid.uuid5(_SALES_DESK_AUDIT_NAMESPACE, export_type),
        action="sales_desk_export",
        after=payload,
    )


def _remove_unserved_export(request: Request) -> None:
    """Remove a generated file when fail-closed auditing blocks its response."""
    export_path = getattr(request.state, "sales_desk_export_path", None)
    if not export_path:
        return
    try:
        Path(export_path).unlink(missing_ok=True)
    except OSError:
        logger.warning(
            "Failed to remove unserved sales desk export",
            extra={"export_path": str(export_path)},
            exc_info=True,
        )


def audit_sales_desk_export(
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
) -> Generator[None, None, None]:
    """Same-session, fail-closed audit around a CSV export request."""
    export_type = _export_type(request.url.path)
    try:
        yield
    except Exception as exc:
        db.rollback()
        try:
            _append_audit(
                db,
                actor_id=current_user.id,
                export_type=export_type,
                payload=_payload(
                    request,
                    export_type=export_type,
                    success=False,
                    failure=type(exc).__name__,
                ),
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Failed to persist failed sales desk export audit")
        _remove_unserved_export(request)
        raise
    else:
        try:
            if db.in_transaction():
                db.commit()
            _append_audit(
                db,
                actor_id=current_user.id,
                export_type=export_type,
                payload=_payload(
                    request,
                    export_type=export_type,
                    success=True,
                    failure=None,
                ),
            )
            db.commit()
        except Exception:
            db.rollback()
            _remove_unserved_export(request)
            logger.exception("Failed to persist successful sales desk export audit")
            raise
