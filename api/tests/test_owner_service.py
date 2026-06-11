"""Tests for OwnerService UoW + storage encapsulation
Verify that the owner write-path flushes without committing (so it composes
inside an outer transaction), that the superseded logo object is only deleted
after the transaction commits, and that logo serving goes through the public
``serve_logo`` API rather than the storage backend directly.
"""

import io
from collections.abc import Iterator

import pytest

from app.common.exceptions import NotFoundException
from app.modules.owner.models import OwnerProfile
from app.modules.owner.service import LogoDownload, OwnerService


class FakeStorage:
    """Minimal storage facade stand-in recording uploads/deletes.

    ``presigned`` toggles the S3-style presigned-URL path; when False the
    facade behaves like the local backend (streamed download, no URL).
    """

    def __init__(self, presigned: bool = False) -> None:
        self._presigned = presigned
        self.uploaded: list[str] = []
        self.deleted: list[str] = []
        self._counter = 0

    def upload_file(self, file_obj, directory: str, filename: str) -> str:
        self._counter += 1
        key = f"{directory}/{filename}-{self._counter}"
        self.uploaded.append(key)
        return key

    def delete_file(self, storage_key: str) -> bool:
        self.deleted.append(storage_key)
        return True

    def download_stream(self, storage_key: str) -> Iterator[bytes]:
        yield b"logo-bytes"

    def presigned_url(self, storage_key: str) -> str | None:
        return f"https://signed/{storage_key}" if self._presigned else None

    def open_file(self, storage_key: str):
        return io.BytesIO(b"logo-bytes")


def _png_upload() -> tuple[io.BytesIO, str, str]:
    """A minimal valid PNG payload accepted by validate_upload."""
    # 1x1 transparent PNG.
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
        b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    return io.BytesIO(png), "logo.png", "image/png"


def test_get_or_create_flushes_without_committing(db):
    storage = FakeStorage()
    service = OwnerService(db, storage=storage)

    profile = service.get_or_create()
    assert profile is not None
    # The row is visible in this session (flushed) but the transaction is
    # still open, so the caller / request owns the commit.
    assert db.get(OwnerProfile, profile.id) is profile
    assert db.in_transaction()


def test_update_does_not_commit(db):
    from app.modules.owner.schemas import OwnerProfileUpdate

    service = OwnerService(db, storage=FakeStorage())
    service.get_or_create()

    service.update(OwnerProfileUpdate(full_name="Acme Ltd"))
    assert db.in_transaction()

    # Rolling back the still-open transaction discards the change, proving the
    # service never committed it itself.
    db.rollback()
    assert service.get_or_create().full_name != "Acme Ltd"


def test_upload_logo_defers_old_object_delete_until_commit(db):
    storage = FakeStorage()
    service = OwnerService(db, storage=storage)

    # First upload: nothing to clean up.
    service.upload_logo(*_png_upload())
    db.commit()
    first_key = storage.uploaded[0]
    assert storage.deleted == []

    # Second upload supersedes the first.
    service.upload_logo(*_png_upload())
    # Before commit the old object must NOT yet be deleted (avoid orphaning
    # the new key if the outer transaction rolls back).
    assert storage.deleted == []

    db.commit()
    # After commit the superseded object is cleaned up exactly once.
    assert storage.deleted == [first_key]


def test_upload_logo_rollback_keeps_old_object(db):
    storage = FakeStorage()
    service = OwnerService(db, storage=storage)
    service.upload_logo(*_png_upload())
    db.commit()
    first_key = storage.uploaded[0]

    service.upload_logo(*_png_upload())
    # Outer transaction aborts: the old object must survive (it's still the
    # one referenced by the committed row).
    db.rollback()
    assert first_key not in storage.deleted


def test_remove_logo_defers_delete_until_commit(db):
    storage = FakeStorage()
    service = OwnerService(db, storage=storage)
    service.upload_logo(*_png_upload())
    db.commit()
    key = storage.uploaded[0]

    service.remove_logo()
    assert storage.deleted == []
    db.commit()
    assert storage.deleted == [key]


def test_serve_logo_streams_for_local_backend(db):
    storage = FakeStorage(presigned=False)
    service = OwnerService(db, storage=storage)
    service.upload_logo(*_png_upload())
    db.commit()

    download = service.serve_logo()
    assert isinstance(download, LogoDownload)
    assert download.presigned_url is None
    assert download.stream is not None
    assert b"".join(download.stream) == b"logo-bytes"


def test_serve_logo_redirects_for_s3_backend(db):
    storage = FakeStorage(presigned=True)
    service = OwnerService(db, storage=storage)
    service.upload_logo(*_png_upload())
    db.commit()

    download = service.serve_logo()
    assert download.stream is None
    assert download.presigned_url is not None
    assert download.presigned_url.startswith("https://signed/")


def test_serve_logo_raises_when_no_logo(db):
    service = OwnerService(db, storage=FakeStorage())
    service.get_or_create()
    db.commit()

    with pytest.raises(NotFoundException):
        service.serve_logo()
