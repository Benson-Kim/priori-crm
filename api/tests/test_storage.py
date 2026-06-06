"""Storage backend tests (W3.3).

The local backend is exercised against a real temp dir. The S3 backend is
driven by a stubbed boto3-style client so no AWS access is needed and the
test runs on every backend (no Postgres dependency).
"""
import io

import pytest

from app.common.exceptions import BadRequestException, NotFoundException
from app.lib.storage import (
    LocalStorageBackend,
    S3StorageBackend,
    StorageService,
    sanitize_storage_key,
)


# ---------------------------------------------------------------------------
# Key sanitisation
# ---------------------------------------------------------------------------


def test_sanitize_storage_key_joins_segments():
    assert sanitize_storage_key("expenses/123", "abc_file.pdf") == "expenses/123/abc_file.pdf"


def test_sanitize_storage_key_strips_traversal():
    # ../ and absolute components are reduced to safe basenames, never escape.
    key = sanitize_storage_key("../../etc", "passwd")
    assert ".." not in key
    assert not key.startswith("/")
    assert key.endswith("passwd")


def test_sanitize_storage_key_rejects_empty():
    with pytest.raises(BadRequestException):
        sanitize_storage_key("", "/")


# ---------------------------------------------------------------------------
# Local backend
# ---------------------------------------------------------------------------


def test_local_backend_round_trip(tmp_path):
    backend = LocalStorageBackend(base_dir=str(tmp_path))
    key = backend.upload_file(io.BytesIO(b"hello world"), "expenses/abc", "doc.pdf")

    assert key == "expenses/abc/doc.pdf"
    assert backend.file_exists(key) is True

    with backend.open_file(key) as fh:
        assert fh.read() == b"hello world"

    assert b"".join(backend.download_stream(key)) == b"hello world"
    assert backend.presigned_url(key) is None

    assert backend.delete_file(key) is True
    assert backend.file_exists(key) is False
    # Idempotent delete of an absent key.
    assert backend.delete_file(key) is True


def test_local_backend_rejects_traversal_key(tmp_path):
    backend = LocalStorageBackend(base_dir=str(tmp_path))
    with pytest.raises(BadRequestException):
        backend.open_file("../../etc/passwd")
    # delete returns False (not an exception) for a bad key.
    assert backend.delete_file("../../etc/passwd") is False


def test_local_backend_missing_file_raises(tmp_path):
    backend = LocalStorageBackend(base_dir=str(tmp_path))
    with pytest.raises(NotFoundException):
        backend.open_file("expenses/abc/missing.pdf")


def test_storage_service_base_dir_back_compat(tmp_path):
    # Facade still accepts base_dir and routes to the local backend.
    service = StorageService(base_dir=str(tmp_path))
    key = service.upload_file(io.BytesIO(b"x"), "d", "f.txt")
    assert service.file_exists(key)
    assert service.delete_file(key)


# ---------------------------------------------------------------------------
# S3 backend (stubbed client)
# ---------------------------------------------------------------------------


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self.closed = False

    def read(self, *_args) -> bytes:
        data, self._data = self._data, b""
        return data

    def iter_chunks(self, chunk_size: int = 65536):
        for i in range(0, len(self._data), chunk_size):
            yield self._data[i : i + chunk_size]

    def close(self) -> None:
        self.closed = True


class _FakeClientError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def upload_fileobj(self, fileobj, bucket, key) -> None:
        self.objects[key] = fileobj.read()

    def get_object(self, Bucket, Key):  # noqa: N803 - boto3 kwarg casing
        if Key not in self.objects:
            raise _FakeClientError("NoSuchKey")
        return {"Body": _FakeBody(self.objects[Key])}

    def delete_object(self, Bucket, Key) -> None:  # noqa: N803
        self.objects.pop(Key, None)

    def head_object(self, Bucket, Key) -> None:  # noqa: N803
        if Key not in self.objects:
            raise _FakeClientError("404")

    def generate_presigned_url(self, op, Params, ExpiresIn):  # noqa: N803
        return f"https://s3.example/{Params['Key']}?expires={ExpiresIn}"


@pytest.fixture
def s3_backend(monkeypatch):
    """An S3StorageBackend wired to the in-memory fake client."""
    import app.lib.storage as storage_mod

    fake = _FakeS3Client()
    backend = S3StorageBackend.__new__(S3StorageBackend)
    backend.bucket = "test-bucket"
    backend._client = fake
    # Make the module's ClientError lookups resolve to our fake error type.
    monkeypatch.setattr(
        "botocore.exceptions.ClientError", _FakeClientError, raising=False
    )
    return backend


def test_s3_backend_round_trip(s3_backend):
    key = s3_backend.upload_file(io.BytesIO(b"s3 bytes"), "expenses/xyz", "r.pdf")
    assert key == "expenses/xyz/r.pdf"
    assert s3_backend.file_exists(key) is True
    assert b"".join(s3_backend.download_stream(key)) == b"s3 bytes"
    url = s3_backend.presigned_url(key)
    assert url and key in url
    assert s3_backend.delete_file(key) is True
    assert s3_backend.file_exists(key) is False


def test_s3_backend_missing_key_raises(s3_backend):
    with pytest.raises(NotFoundException):
        list(s3_backend.download_stream("expenses/xyz/missing.pdf"))


def test_s3_backend_rejects_bad_key_on_delete(s3_backend):
    assert s3_backend.delete_file("") is False
