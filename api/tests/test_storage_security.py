"""File-storage attack-surface hardening.

These tests exercise the centralized upload guards (common/uploads.py) and
the path-confinement guarantees of StorageService (lib/storage.py). They are
pure-filesystem/logic tests (no DB), so they run identically on SQLite and
PostgreSQL.
"""

import io

import pytest

from app.common.exceptions import BadRequestException, NotFoundException
from app.common.uploads import (
    MAX_UPLOAD_SIZE_BYTES,
    sanitize_basename,
    validate_upload,
)
from app.lib.storage import StorageService

# Magic-byte prefixes for crafting valid sample payloads.
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PDF_MAGIC = b"%PDF-1.4\n"


@pytest.fixture
def storage(tmp_path):
    """A StorageService confined to a throwaway temp directory."""
    return StorageService(base_dir=str(tmp_path))


# filename sanitization


class TestSanitizeBasename:
    def test_strips_posix_traversal(self):
        assert sanitize_basename("../../etc/passwd") == "passwd"

    def test_strips_windows_separators(self):
        assert sanitize_basename(r"..\\..\\windows\\system32\\cmd.exe") == "cmd.exe"

    def test_rejects_dotfile(self):
        assert sanitize_basename(".env") == "unnamed_file"

    def test_empty_falls_back(self):
        assert sanitize_basename("") == "unnamed_file"
        assert sanitize_basename(None) == "unnamed_file"

    def test_plain_name_preserved(self):
        assert sanitize_basename("receipt.pdf") == "receipt.pdf"


# StorageService path confinement


class TestStorageContainment:
    def test_upload_round_trips_within_base_dir(self, storage):
        key = storage.upload_file(io.BytesIO(b"hello"), "expenses/abc", "r.txt")
        # Stored key is relative to base_dir and never escapes it.
        assert not key.startswith("/")
        assert ".." not in key
        with storage.open_file(key) as fh:
            assert fh.read() == b"hello"
        assert storage.delete_file(key) is True
        assert storage.file_exists(key) is False

    def test_upload_neutralizes_traversal_in_parts(self, storage):
        key = storage.upload_file(io.BytesIO(b"x"), "../../../etc", "../../passwd")
        resolved = storage.backend.resolve_safe_path(key)
        assert storage.backend.base_dir in resolved.parents

    def test_open_traversal_key_rejected(self, storage):
        with pytest.raises(BadRequestException):
            storage.backend.resolve_safe_path("../../../../etc/passwd")

    def test_open_absolute_outside_rejected(self, storage):
        with pytest.raises(BadRequestException):
            storage.backend.resolve_safe_path("/etc/passwd")

    def test_open_missing_file_raises_not_found(self, storage):
        with pytest.raises(NotFoundException):
            storage.open_file("expenses/abc/missing.txt")

    def test_delete_traversal_key_refused(self, storage):
        assert storage.delete_file("../../etc/passwd") is False


# upload content guards


class TestValidateUpload:
    def test_valid_png_accepted(self):
        payload = PNG_MAGIC + b"\x00" * 32
        name, ext, size = validate_upload(io.BytesIO(payload), "logo.png", "image/png")
        assert name == "logo.png"
        assert ext == ".png"
        assert size == len(payload)

    def test_html_masquerading_as_png_rejected(self):
        payload = b"<!DOCTYPE html><script>alert(1)</script>"
        with pytest.raises(BadRequestException):
            validate_upload(io.BytesIO(payload), "evil.png", "image/png")

    def test_disallowed_extension_rejected(self):
        with pytest.raises(BadRequestException):
            validate_upload(io.BytesIO(b"MZ..."), "malware.exe", "application/pdf")

    def test_disallowed_mime_rejected(self):
        payload = PDF_MAGIC + b"x"
        with pytest.raises(BadRequestException):
            validate_upload(io.BytesIO(payload), "f.pdf", "application/x-msdownload")

    def test_empty_file_rejected(self):
        with pytest.raises(BadRequestException):
            validate_upload(io.BytesIO(b""), "empty.pdf", "application/pdf")

    def test_oversize_file_rejected(self):
        payload = PDF_MAGIC + b"0" * (MAX_UPLOAD_SIZE_BYTES + 1)
        with pytest.raises(BadRequestException):
            validate_upload(io.BytesIO(payload), "big.pdf", "application/pdf")

    def test_executable_smuggled_as_text_rejected(self):
        payload = b"\x7fELF\x02\x01\x01"
        with pytest.raises(BadRequestException):
            validate_upload(io.BytesIO(payload), "notes.txt", "text/plain")

    def test_stream_rewound_after_validation(self):
        payload = PDF_MAGIC + b"body"
        buf = io.BytesIO(payload)
        validate_upload(buf, "f.pdf", "application/pdf")
        assert buf.tell() == 0
        assert buf.read() == payload
