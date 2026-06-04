"""Centralized upload validation (V-DRY-3).

Single source of truth for the file-upload attack surface: extension,
MIME, size, emptiness, and magic-byte content sniffing. Both the API
routers and the storage layer consume these helpers so the rules cannot
drift apart between call sites.
"""
import os
from typing import BinaryIO

from app.common.exceptions import BadRequestException

# 10 MB hard cap on any single uploaded file.
MAX_UPLOAD_SIZE_BYTES = 10_485_760

ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/plain",
        "text/csv",
    }
)

ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".txt",
        ".csv",
    }
)

# Magic-byte (file signature) prefixes for the binary formats we accept.
# Used to detect a payload whose real type contradicts its claimed MIME
# (e.g. an HTML/script file mislabeled as image/png). Text-based formats
# (text/plain, text/csv) have no reliable signature and are validated by
# rejecting binary/executable/markup signatures instead.
_MAGIC_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF-",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": (b"RIFF",),  # RIFF....WEBP — prefix + WEBP at offset 8
    # OOXML (docx/xlsx) are PKZIP archives.
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (b"PK\x03\x04",),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (b"PK\x03\x04",),
    # Legacy OLE2 compound documents (.doc/.xls).
    "application/msword": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
    "application/vnd.ms-excel": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
}

# Signatures we never allow for a text/* upload — catches a script, markup,
# or native binary smuggled in as text/plain or text/csv.
_FORBIDDEN_TEXT_SIGNATURES: tuple[bytes, ...] = (
    b"MZ",  # Windows PE executable / DLL
    b"\x7fELF",  # ELF executable
    b"\xca\xfe\xba\xbe",  # Mach-O / Java class
    b"#!",  # shell / interpreter shebang
    b"<!doctype",  # HTML (compared case-insensitively)
    b"<html",
    b"<script",
    b"<?php",
)

_SNIFF_BYTES = 512


def sanitize_basename(raw_filename: str | None) -> str:
    """Reduce an untrusted filename to a safe basename.

    Strips any directory components (``../``, absolute paths, Windows
    separators) and rejects dotfiles, returning a stable fallback when the
    result is empty.
    """
    name = (raw_filename or "").replace("\\", "/")
    name = os.path.basename(name)
    if not name or name.startswith("."):
        return "unnamed_file"
    return name


def validate_extension(filename: str) -> str:
    """Validate the file extension against the allow-list.

    Returns the lower-cased extension (including the leading dot).
    """
    _, ext = os.path.splitext(filename)
    ext_lower = ext.lower()
    if ext_lower not in ALLOWED_EXTENSIONS:
        raise BadRequestException(
            detail=(
                f"File type '{ext_lower or '(none)'}' is not allowed. "
                f"Accepted: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            ),
            field="file",
        )
    return ext_lower


def validate_mime_type(mime_type: str) -> str:
    """Validate the claimed MIME type against the allow-list."""
    if mime_type not in ALLOWED_MIME_TYPES:
        raise BadRequestException(
            detail=(
                f"MIME type '{mime_type}' is not allowed. "
                "Upload a PDF, image, or office document."
            ),
            field="file",
        )
    return mime_type


def validate_size(file_size_bytes: int) -> int:
    """Validate the file is non-empty and within the size cap."""
    if file_size_bytes <= 0:
        raise BadRequestException(detail="Uploaded file is empty.", field="file")
    if file_size_bytes > MAX_UPLOAD_SIZE_BYTES:
        raise BadRequestException(
            detail=(
                f"File size ({file_size_bytes / 1_048_576:.1f} MB) exceeds "
                f"the {MAX_UPLOAD_SIZE_BYTES / 1_048_576:.0f} MB limit."
            ),
            field="file",
        )
    return file_size_bytes


def sniff_size(file_obj: BinaryIO) -> int:
    """Measure the byte length of a seekable file object, then rewind it."""
    file_obj.seek(0, os.SEEK_END)
    size = file_obj.tell()
    file_obj.seek(0)
    return size


def validate_content(file_obj: BinaryIO, claimed_mime: str) -> None:
    """Content-sniff the payload's magic bytes against the claimed MIME.

    Rejects a file whose real signature contradicts what the client says it
    is (e.g. HTML or an executable masquerading as image/png). The stream is
    rewound to position 0 before returning so callers can persist it.
    """
    head = file_obj.read(_SNIFF_BYTES)
    file_obj.seek(0)

    if not head:
        raise BadRequestException(detail="Uploaded file is empty.", field="file")

    signatures = _MAGIC_SIGNATURES.get(claimed_mime)
    if signatures is not None:
        if claimed_mime == "image/webp":
            ok = head.startswith(b"RIFF") and head[8:12] == b"WEBP"
        else:
            ok = any(head.startswith(sig) for sig in signatures)
        if not ok:
            raise BadRequestException(
                detail=(
                    "File content does not match its declared type "
                    f"('{claimed_mime}'). The upload was rejected."
                ),
                field="file",
            )
        return

    # Text formats: no positive signature, so reject anything that looks
    # like markup, a script, or a native binary.
    lowered = head.lstrip().lower()
    for sig in _FORBIDDEN_TEXT_SIGNATURES:
        if head.startswith(sig) or lowered.startswith(sig.lower()):
            raise BadRequestException(
                detail=(
                    "File content does not match its declared type "
                    f"('{claimed_mime}'). The upload was rejected."
                ),
                field="file",
            )


def validate_upload(
    file_obj: BinaryIO,
    filename: str,
    claimed_mime: str,
) -> tuple[str, str, int]:
    """Run the full upload guard chain and return (safe_basename, ext, size).

    Order: sanitize name → extension → MIME → size → content-sniff. The
    ``file_obj`` is left rewound to position 0, ready to persist.
    """
    safe_basename = sanitize_basename(filename)
    ext = validate_extension(safe_basename)
    validate_mime_type(claimed_mime)
    size = sniff_size(file_obj)
    validate_size(size)
    validate_content(file_obj, claimed_mime)
    return safe_basename, ext, size
