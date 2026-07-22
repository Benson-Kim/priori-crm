"""Authorization and append-only access auditing for financial reports."""

from pathlib import Path
import logging
import uuid
from collections.abc import Generator
from datetime import date

from fastapi import Request

from app.common.audit import record_audit_event
from app.common.dependencies import CurrentUser, DbSession
from app.common.exceptions import BadRequestException, ForbiddenException
from app.common.reporting_time import reporting_date
from app.constants.enums import PRIVILEGED_ROLES, UserRole
from app.modules.statements.schemas import RangePreset, ResolvedPeriod

logger = logging.getLogger(__name__)

REPORT_VIEW_ROLES = PRIVILEGED_ROLES
REPORT_EXPORT_ROLES = PRIVILEGED_ROLES
_REPORT_AUDIT_NAMESPACE = uuid.UUID("1d859bbe-b71b-4b37-9ab7-b6dc8eb81e68")
_FILTER_KEYS = ("status", "source", "strict", "page", "per_page", "withTotal")


def _report_type(path: str) -> str:
    if "/aged-receivables" in path:
        return "aged_receivables"
    if "/aged-payables" in path:
        return "aged_payables"
    if "/purchases" in path:
        return "purchases"
    if "/taxes" in path:
        return "vat_reconciliation_estimate"
    return "sales"


def _period_context(request: Request) -> dict[str, str | None]:
    if "/aged-" in request.url.path:
        return {"as_of_date": reporting_date().isoformat()}

    raw_range = request.query_params.get("range", RangePreset.THIS_MONTH.value)
    raw_from = request.query_params.get("dateFrom")
    raw_to = request.query_params.get("dateTo")
    try:
        period = ResolvedPeriod.resolve(
            RangePreset(raw_range),
            date.fromisoformat(raw_from) if raw_from else None,
            date.fromisoformat(raw_to) if raw_to else None,
            today=reporting_date(),
        )
        return {
            "date_from": period.date_from.isoformat(),
            "date_to": period.date_to.isoformat(),
        }
    except (BadRequestException, ValueError, TypeError):
        # Endpoint validation remains authoritative; the audit trail keeps the
        # submitted values for a failed request without turning auditing into a
        # second request parser.
        return {"date_from": raw_from, "date_to": raw_to, "range": raw_range}


def _filters(request: Request) -> dict[str, str | bool]:
    filters: dict[str, str | bool] = {
        key: request.query_params[key]
        for key in _FILTER_KEYS
        if key in request.query_params
    }
    if request.query_params.get("search"):
        filters["search_present"] = True
    return filters


def _append_audit(
    db: DbSession,
    *,
    actor_id,
    report_type: str,
    action: str,
    payload: dict,
) -> None:
    """Append one audit row using the request-scoped database session."""
    record_audit_event(
        db,
        actor_id=actor_id,
        entity_type="financial_report",
        entity_id=uuid.uuid5(_REPORT_AUDIT_NAMESPACE, report_type),
        action=action,
        after=payload,
    )


def _payload(
    request: Request,
    *,
    report_type: str,
    success: bool,
    failure: str | None,
    period: dict[str, str | None],
) -> dict:
    return {
        "request_id": getattr(request.state, "request_id", None),
        "success": success,
        "failure": failure,
        "report_type": report_type,
        "period": period,
        "currency": (
            "KES"
            if report_type == "vat_reconciliation_estimate"
            else request.query_params.get("currency", "KES")
        ),
        "filters": _filters(request),
        "export_metrics": getattr(request.state, "report_export_metrics", None),
        "deployment_boundary": "single_organization",
    }


def _remove_unserved_export(request: Request) -> None:
    """Remove a generated file when fail-closed auditing blocks its response."""
    export_path = getattr(request.state, "report_export_path", None)
    if not export_path:
        return

    try:
        Path(export_path).unlink(missing_ok=True)
    except OSError:
        logger.warning(
            "Failed to remove unserved report export",
            extra={"export_path": str(export_path)},
            exc_info=True,
        )


def authorize_and_audit_report_access(
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
) -> Generator[None, None, None]:
    """Default-deny report access with same-session, fail-closed auditing."""
    action = "report_export" if request.url.path.endswith("/export") else "report_view"
    report_type = _report_type(request.url.path)
    period = _period_context(request)
    try:
        role = UserRole(current_user.role)
        allowed = (
            REPORT_EXPORT_ROLES if action == "report_export" else REPORT_VIEW_ROLES
        )
        if role not in allowed:
            raise ForbiddenException(
                "Financial reports require an administrator or manager role"
            )
        yield
    except Exception as exc:
        db.rollback()
        try:
            _append_audit(
                db,
                actor_id=current_user.id,
                report_type=report_type,
                action=action,
                payload=_payload(
                    request,
                    report_type=report_type,
                    success=False,
                    failure=type(exc).__name__,
                    period=period,
                ),
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Failed to persist failed financial report audit")
        _remove_unserved_export(request)
        raise
    else:
        try:
            if db.in_transaction():
                db.commit()
            _append_audit(
                db,
                actor_id=current_user.id,
                report_type=report_type,
                action=action,
                payload=_payload(
                    request,
                    report_type=report_type,
                    success=True,
                    failure=None,
                    period=period,
                ),
            )
            db.commit()
        except Exception:
            db.rollback()
            _remove_unserved_export(request)
            logger.exception("Failed to persist successful financial report audit")
            raise