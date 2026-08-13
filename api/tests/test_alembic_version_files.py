"""Filename/revision-id consistency for alembic version files (issue #55).

Alembic reads the ``revision`` attribute, never the filename, so a filename
whose hash segment disagrees with the file's actual revision id is invisible
to migrations — but it makes ``ls``-based collision checks unreliable and
already produced a false "duplicate revision id" report (three filenames on
develop collided with other files' revision ids). This test pins the rule:
the filename must say what the file contains.

Pure text parsing — no database, no importing of migration modules.
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"

#: Dated version files: ``YYYY_MM_DD_HHMM-<revision>_<slug>.py``.
_DATED_NAME = re.compile(r"^\d{4}_\d{2}_\d{2}_\d{4}-([0-9a-f]+)_")

#: The ``revision`` assignment inside a version file (plain or annotated).
_REVISION_LINE = re.compile(
    r'^revision(?:\s*:\s*str)?\s*=\s*["\']([^"\']+)["\']', re.MULTILINE
)


def _version_files() -> list[Path]:
    files = sorted(
        path
        for path in VERSIONS_DIR.iterdir()
        if path.suffix == ".py" and path.name != "__init__.py"
    )
    assert files, f"No alembic version files found under {VERSIONS_DIR}"
    return files


def _actual_revision(path: Path) -> str:
    match = _REVISION_LINE.search(path.read_text(encoding="utf-8"))
    assert match, f"{path.name}: could not find a revision assignment"
    return match.group(1)


def test_filename_hash_matches_revision_id() -> None:
    """Every version file's name must carry its own revision id.

    Dated files must embed the revision id as the hash segment; any other
    version file (the initial migration) must use the revision id as its
    stem. A mismatch is exactly the false-duplicate trap #55 documents.
    """
    mismatches = []
    for path in _version_files():
        revision = _actual_revision(path)
        dated = _DATED_NAME.match(path.name)
        named = dated.group(1) if dated else path.stem
        if named != revision:
            mismatches.append(
                f"{path.name}: filename says {named!r}, revision is {revision!r}"
            )
    assert not mismatches, (
        "Version filename hash segment must equal the file's revision id "
        "(#55):\n" + "\n".join(mismatches)
    )


def test_revision_ids_are_unique() -> None:
    """No two version files may declare the same revision id."""
    seen: dict[str, str] = {}
    duplicates = []
    for path in _version_files():
        revision = _actual_revision(path)
        if revision in seen:
            duplicates.append(f"{revision!r} in {seen[revision]} and {path.name}")
        else:
            seen[revision] = path.name
    assert not duplicates, "Duplicate revision id(s): " + "; ".join(duplicates)
