"""Transaction-scoped object-storage cleanup (ISSUE-006).

Generalizes the ``after_commit`` cleanup hook OwnerService introduced for
logo uploads so any module can keep object storage consistent with the
outcome of the surrounding DB transaction:

- ``schedule_delete_on_commit`` — delete *superseded* objects only once the
  transaction actually commits (replace-style writes, e.g. logo upload).
  Deleting inline would orphan the new key if the transaction later rolled
  back; if the transaction rolls back instead, the pending deletes are
  discarded because the supersede never became durable.
- ``schedule_delete_on_rollback`` — delete *freshly written* objects if the
  transaction rolls back (write-storage-then-insert flows, e.g. expense
  document attachments). Without this, a failed insert or commit leaves the
  new object orphaned in storage. On commit the pending deletes are
  discarded because the rows referencing the objects are now durable.

Both helpers are best-effort on the storage side: a failed delete is
logged, never raised — the DB outcome is already decided when the hooks
run.
"""

from __future__ import annotations

import logging
from typing import Protocol

from sqlalchemy import event
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_ON_COMMIT_KEY = "storage_tx_delete_on_commit"
_ON_ROLLBACK_KEY = "storage_tx_delete_on_rollback"


class StorageFacade(Protocol):
    """The single storage capability these helpers rely on."""

    def delete_file(self, storage_key: str) -> bool: ...


def _best_effort_delete(storage: StorageFacade, keys: list[str]) -> None:
    for key in keys:
        try:
            storage.delete_file(key)
        except Exception:
            logger.warning(
                "Transaction-scoped storage cleanup failed — orphan may remain",
                extra={"storage_key": key},
                exc_info=True,
            )


def _register(
    db: Session,
    storage: StorageFacade,
    keys: tuple[str | None, ...],
    info_key: str,
    drain_event: str,
    discard_event: str,
) -> None:
    """Accumulate keys on the session and attach one-shot drain/discard hooks.

    The pending keys live in ``Session.info`` so repeated calls within the
    same transaction share one listener pair. ``drain_event`` deletes the
    accumulated keys; ``discard_event`` clears them (the opposite outcome
    makes the deletes unnecessary or wrong).
    """
    targets = [k for k in keys if k]
    if not targets:
        return

    pending: list[str] = db.info.setdefault(info_key, [])
    already_registered = bool(pending)
    pending.extend(targets)
    if already_registered:
        # Hooks are already attached for this session; they drain the list.
        return

    @event.listens_for(db, drain_event, once=True)
    def _drain(session: Session) -> None:
        _best_effort_delete(storage, session.info.pop(info_key, []))

    @event.listens_for(db, discard_event, once=True)
    def _discard(session: Session) -> None:
        session.info.pop(info_key, None)


def schedule_delete_on_commit(
    db: Session, storage: StorageFacade, *keys: str | None
) -> None:
    """Delete superseded storage objects once the transaction commits.

    A rollback discards the scheduled deletes: the supersede never became
    durable, so the old objects are still the live ones. ``None``/empty
    keys are ignored.
    """
    _register(db, storage, keys, _ON_COMMIT_KEY, "after_commit", "after_rollback")


def schedule_delete_on_rollback(
    db: Session, storage: StorageFacade, *keys: str | None
) -> None:
    """Delete freshly written storage objects if the transaction rolls back.

    A commit discards the scheduled deletes: the rows referencing the new
    objects are now durable, so the objects must be kept. ``None``/empty
    keys are ignored.
    """
    _register(db, storage, keys, _ON_ROLLBACK_KEY, "after_rollback", "after_commit")
