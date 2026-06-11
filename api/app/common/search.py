r"""Shared, LIKE-safe search helpers.

Every list endpoint previously built ``column.ilike(f"%{term}%")`` directly.
That has two problems:

1. **Not LIKE-safe** - the wildcard metacharacters ``%`` and ``_`` (and the
   escape character ``\``) in user input were interpreted by the pattern
   engine, so a search for ``50%`` or ``foo_bar`` silently behaved as a
   wildcard query instead of a literal substring search.
2. **Duplicated** - the same ``or_(col.ilike(...), ...)`` block was copied
   across vendors / invoices / quotes / expenses / customers, so a fix had to
   be made in five places.

This module provides a single escaped implementation. Combined with the
``pg_trgm`` GIN indexes added alongside it, ``ILIKE '%term%'`` can use a
trigram index instead of a full scan per keystroke.
"""

from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.sql.elements import ColumnElement

# Backslash is the escape character we declare to ``ILIKE`` / ``LIKE`` below,
# so it must be escaped first to avoid double-escaping the % and _ that follow.
_LIKE_ESCAPE_CHAR = "\\"


def escape_like(term: str) -> str:
    """Escape LIKE/ILIKE wildcard metacharacters in a user-supplied term.

    Escapes the escape character itself first, then ``%`` and ``_`` so they are
    matched literally. The returned value is meant to be wrapped with ``%`` for
    a substring search and used with ``escape="\\"``.

    >>> escape_like("50%")
    '50\\\\%'
    >>> escape_like("foo_bar")
    'foo\\\\_bar'
    """
    return (
        term.replace(_LIKE_ESCAPE_CHAR, _LIKE_ESCAPE_CHAR * 2)
        .replace("%", _LIKE_ESCAPE_CHAR + "%")
        .replace("_", _LIKE_ESCAPE_CHAR + "_")
    )


def like_pattern(term: str) -> str:
    """Return a LIKE-safe ``%term%`` substring pattern for *term*.

    The term is trimmed and its wildcard metacharacters escaped, so the result
    matches *term* literally as a substring.
    """
    return f"%{escape_like(term.strip())}%"


def build_search_clause(
    term: str,
    *columns: ColumnElement,
) -> ColumnElement[bool] | None:
    """Build a case-insensitive, LIKE-safe ``OR`` substring search clause.

    Returns an ``or_(col.ilike(pattern, escape=...), ...)`` element across the
    given *columns*, or ``None`` when *term* is empty/blank (so callers can skip
    filtering and, importantly, avoid joining related tables that are only
    needed for the search).

    Example::

        clause = build_search_clause(
            filters.search,
            Vendor.vendor_name,
            Vendor.email,
        )
        if clause is not None:
            query = query.filter(clause)
    """
    if not term or not term.strip():
        return None

    pattern = like_pattern(term)
    return or_(*(col.ilike(pattern, escape=_LIKE_ESCAPE_CHAR) for col in columns))
