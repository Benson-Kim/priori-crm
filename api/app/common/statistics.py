"""Shared read-side aggregation helpers.

Small, pure-ish utilities used by multiple module services to avoid
re-implementing the same aggregation shape. Keeping them here is a DRY
measure: the customer and vendor services previously hand-rolled an
identical ``group_by(status) -> dict -> sum`` block, which is exactly the
kind of incidental duplication that drifts over time (one side gets a new
status bucket, the other silently under-counts ``all``).

These helpers are intentionally model-agnostic: callers pass the ORM
status column and map the returned dict onto their own typed
``*StatusCounts`` schema, so each module keeps ownership of its status
vocabulary (SRP) while sharing the mechanics.
"""

from __future__ import annotations

import logging

from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.common.exceptions import DatabaseException

logger = logging.getLogger(__name__)


def status_counts(
    db: Session,
    status_column: ColumnElement,
    id_column: ColumnElement,
    *,
    error_context: str,
) -> dict[str, int]:
    """Return ``{status_value: count}`` for one status-bucketed table.

    Groups by ``status_column`` and counts ``id_column`` per bucket. The
    caller maps the result onto its own typed counts schema and derives
    ``all`` as ``sum(result.values())`` so the total always reflects every
    status the table actually contains (it stays correct if the status set
    later grows).

    ``error_context`` is folded into the raised ``DatabaseException`` and
    the log line so failures are attributable to a specific entity.
    """
    try:
        rows = (
            db.query(status_column, func.count(id_column)).group_by(status_column).all()
        )
        return {status: count for status, count in rows}
    except SQLAlchemyError as exc:
        logger.exception("Database error getting %s status counts", error_context)
        raise DatabaseException(f"Failed to get {error_context} status counts") from exc
