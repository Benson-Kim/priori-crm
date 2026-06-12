"""Tests for the shared transaction-scoped storage cleanup (ISSUE-006).

``schedule_delete_on_commit`` generalizes the OwnerService logo hook
(delete the *superseded* object only once the transaction commits);
``schedule_delete_on_rollback`` is the new-key cleanup used by expense
document attachments (delete the *freshly written* object if the
transaction rolls back, keep it once committed).
"""

import pytest
from sqlalchemy import text

from app.common.storage_tx import (
    schedule_delete_on_commit,
    schedule_delete_on_rollback,
)

# Pure session/event mechanics — no schema needed.
pytestmark = pytest.mark.no_db


class FakeStorage:
    """Records deletes; stands in for the storage facade."""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete_file(self, storage_key: str) -> bool:
        self.deleted.append(storage_key)
        return True


class ExplodingStorage:
    def delete_file(self, storage_key: str) -> bool:
        raise RuntimeError("storage unavailable")


class TestDeleteOnRollback:
    def test_key_deleted_after_rollback_not_inline(self, db):
        storage = FakeStorage()
        db.execute(text("SELECT 1"))  # begin the transaction
        schedule_delete_on_rollback(db, storage, "expenses/e1/new.pdf")

        assert storage.deleted == []  # nothing happens inline
        db.rollback()
        assert storage.deleted == ["expenses/e1/new.pdf"]

    def test_key_kept_after_commit(self, db):
        storage = FakeStorage()
        db.execute(text("SELECT 1"))
        schedule_delete_on_rollback(db, storage, "expenses/e1/new.pdf")

        db.commit()
        assert storage.deleted == []

        # A rollback later in the same session must not delete the
        # now-committed object.
        db.execute(text("SELECT 1"))
        db.rollback()
        assert storage.deleted == []

    def test_keys_accumulate_and_none_is_ignored(self, db):
        storage = FakeStorage()
        db.execute(text("SELECT 1"))
        schedule_delete_on_rollback(db, storage, "a")
        schedule_delete_on_rollback(db, storage, None, "b")

        db.rollback()
        assert storage.deleted == ["a", "b"]

    def test_failed_delete_is_logged_not_raised(self, db):
        db.execute(text("SELECT 1"))
        schedule_delete_on_rollback(db, ExplodingStorage(), "a")
        db.rollback()  # must not raise


class TestDeleteOnCommit:
    def test_key_deleted_only_after_commit(self, db):
        storage = FakeStorage()
        db.execute(text("SELECT 1"))
        schedule_delete_on_commit(db, storage, "owner/logo/old.png")

        assert storage.deleted == []
        db.commit()
        assert storage.deleted == ["owner/logo/old.png"]

    def test_rollback_discards_pending_on_commit_deletes(self, db):
        storage = FakeStorage()
        db.execute(text("SELECT 1"))
        schedule_delete_on_commit(db, storage, "owner/logo/old.png")

        db.rollback()
        assert storage.deleted == []

        # The transaction that scheduled the supersede was rolled back, so
        # a later commit must not delete the still-live object.
        db.execute(text("SELECT 1"))
        db.commit()
        assert storage.deleted == []
