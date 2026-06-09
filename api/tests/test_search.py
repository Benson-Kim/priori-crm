"""Tests for LIKE-safe search (W3.1, P-2).

Two layers:

1. Pure-logic unit tests for ``escape_like`` / ``like_pattern`` /
   ``build_search_clause`` - run on any backend.
2. A behavioural test proving that ``%`` and ``_`` in a search term are matched
   *literally* (not as wildcards) through ``VendorService.list_vendors``. This
   exercises the real ``ILIKE ... ESCAPE`` path and is Postgres-guarded to match
   the production database (the SQLite fallback's ILIKE/ESCAPE semantics differ).
"""

import pytest

from app.common.pagination import PaginationParams
from app.common.search import build_search_clause, escape_like, like_pattern
from app.constants.enums import Currency, VendorStatus
from app.modules.vendors.models import Vendor
from app.modules.vendors.schemas import VendorFilterParams
from app.modules.vendors.service import VendorService
from tests.conftest import USING_POSTGRES

# Unit tests (all backends)


def test_escape_like_escapes_wildcards():
    assert escape_like("50%") == "50\\%"
    assert escape_like("foo_bar") == "foo\\_bar"
    # The escape char itself is escaped first to avoid double-escaping.
    assert escape_like("a\\b") == "a\\\\b"
    assert escape_like("plain") == "plain"


def test_like_pattern_wraps_and_escapes():
    assert like_pattern("  50% ") == "%50\\%%"
    assert like_pattern("acme") == "%acme%"


def test_build_search_clause_none_for_blank():
    assert build_search_clause("", Vendor.vendor_name) is None
    assert build_search_clause("   ", Vendor.vendor_name) is None


def test_build_search_clause_returns_clause_for_term():
    clause = build_search_clause("acme", Vendor.vendor_name, Vendor.email)
    assert clause is not None


# Behavioural test (Postgres only)


@pytest.mark.skipif(
    not USING_POSTGRES,
    reason="ILIKE ... ESCAPE wildcard semantics are validated against Postgres.",
)
def test_percent_in_term_is_matched_literally(db):
    """A search for '50%' must match the literal vendor, not every vendor."""
    service = VendorService(db)

    literal = Vendor(
        vendor_name="Discount 50% Supplies",
        currency=Currency.KES,
        status=VendorStatus.ACTIVE,
        version=1,
    )
    other = Vendor(
        vendor_name="Regular Supplies",
        currency=Currency.KES,
        status=VendorStatus.ACTIVE,
        version=1,
    )
    db.add_all([literal, other])
    db.flush()

    result = service.list_vendors(
        PaginationParams(page=1, per_page=50),
        VendorFilterParams(search="50%"),
    )
    names = {item.vendor_name for item in result.items}

    assert "Discount 50% Supplies" in names
    # Were '%' treated as a wildcard, 'Regular Supplies' would also match.
    assert "Regular Supplies" not in names


@pytest.mark.skipif(
    not USING_POSTGRES,
    reason="ILIKE ... ESCAPE wildcard semantics are validated against Postgres.",
)
def test_underscore_in_term_is_matched_literally(db):
    """'_' must match a literal underscore, not any single character."""
    service = VendorService(db)

    literal = Vendor(
        vendor_name="ACME_INC",
        currency=Currency.KES,
        status=VendorStatus.ACTIVE,
        version=1,
    )
    decoy = Vendor(
        vendor_name="ACMEXINC",
        currency=Currency.KES,
        status=VendorStatus.ACTIVE,
        version=1,
    )
    db.add_all([literal, decoy])
    db.flush()

    result = service.list_vendors(
        PaginationParams(page=1, per_page=50),
        VendorFilterParams(search="ACME_INC"),
    )
    names = {item.vendor_name for item in result.items}

    assert "ACME_INC" in names
    # Were '_' a single-char wildcard, 'ACMEXINC' would also match.
    assert "ACMEXINC" not in names


@pytest.mark.skipif(
    not USING_POSTGRES,
    reason="ILIKE ... ESCAPE wildcard semantics are validated against Postgres.",
)
def test_plain_term_still_matches_substring(db):
    """Ordinary substring search continues to work after escaping."""
    service = VendorService(db)

    db.add(
        Vendor(
            vendor_name="Nairobi Hardware",
            currency=Currency.KES,
            status=VendorStatus.ACTIVE,
            version=1,
        )
    )
    db.flush()

    result = service.list_vendors(
        PaginationParams(page=1, per_page=50),
        VendorFilterParams(search="hardware"),
    )
    names = {item.vendor_name for item in result.items}
    assert "Nairobi Hardware" in names
