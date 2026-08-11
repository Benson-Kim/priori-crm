"""Billing-profile code building + one-off backfill for existing customers.

Shared between the live create path (:mod:`app.modules.customers.service`)
and the Alembic backfill migration so both issue codes from the SAME
``reference_sequences`` scope and the SAME seed defaults
(:data:`app.constants.settings_defaults.DEFAULT_BILLING_PROFILE_DEFAULTS`).

The prototype's 3-letter name slug alone is collision-prone ("Acme Ltd" and
"Acme Group" both become ``ACM``), so the code appends a monotonic,
never-reused numeric suffix from the persisted high-water-mark store:
``<SLUG>-<NNNN>-<CUR>`` (e.g. ``BAR-0007-USD``). Both of a customer's
profiles share one suffix, differing only in the currency segment.

The backfill deliberately uses lightweight ``sa.table()`` clauses (not the
ORM models) so the migration keeps working even if the models evolve later.
"""

import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from app.constants.enums import BillingCurrency
from app.constants.settings_defaults import DEFAULT_BILLING_PROFILE_DEFAULTS

#: reference_sequences scope (also the advisory-lock key) for profile codes.
PROFILE_CODE_SCOPE_KEY = "customer_billing_profile_code"

#: Zero-pad width of the numeric suffix segment.
PROFILE_CODE_SUFFIX_WIDTH = 4

_customers = sa.table(
    "customers",
    sa.column("id"),
    sa.column("company_name"),
    sa.column("first_name"),
    sa.column("last_name"),
    sa.column("created_at"),
)

_profiles = sa.table(
    "customer_billing_profiles",
    sa.column("id"),
    sa.column("customer_id"),
    sa.column("currency"),
    sa.column("code"),
    sa.column("payment_terms"),
    sa.column("tax_treatment"),
    sa.column("credit_limit"),
    sa.column("synced"),
    sa.column("synced_at"),
    sa.column("version"),
    sa.column("created_at"),
    sa.column("updated_at"),
)

_sequences = sa.table(
    "reference_sequences",
    sa.column("scope_key"),
    sa.column("last_value"),
)


def profile_code_slug(display_name: str | None) -> str:
    """Reduce a customer display name to the 3-letter code slug.

    Mirrors the prototype's ``slugOf`` (letters only, first three,
    uppercased, ``CUS`` fallback); uniqueness comes from the numeric
    suffix, not from this slug.
    """
    letters = re.sub(r"[^A-Za-z]", "", display_name or "")
    return letters[:3].upper() or "CUS"


def build_profile_code(slug: str, suffix: int, currency: str) -> str:
    """Format a profile code: ``<SLUG>-<NNNN>-<CUR>``."""
    return f"{slug}-{suffix:0{PROFILE_CODE_SUFFIX_WIDTH}d}-{currency}"


def default_profile_values(currency: str) -> dict[str, str | Decimal]:
    """Seed payment terms / tax treatment / credit limit for one currency.

    ``credit_limit`` is returned as a Decimal built from the string constant
    (never a float) and is denominated in the profile's own currency.
    """
    defaults = DEFAULT_BILLING_PROFILE_DEFAULTS[currency]
    return {
        "payment_terms": defaults["payment_terms"],
        "tax_treatment": defaults["tax_treatment"],
        "credit_limit": Decimal(defaults["credit_limit"]),
    }


def backfill_billing_profiles(bind: Connection) -> int:
    """Create both USD and KES profiles for customers that have none.

    Idempotent: customers that already own at least one profile are skipped
    entirely (the create path always writes both atomically, so "one profile
    exists" means the pair exists). Advances the ``reference_sequences``
    high-water mark past every suffix issued here so the live generator can
    never re-issue a backfilled suffix.

    Returns the number of customers backfilled.
    """
    has_profile = (
        sa.select(sa.literal(1))
        .select_from(_profiles)
        .where(_profiles.c.customer_id == _customers.c.id)
        .exists()
    )
    rows = bind.execute(
        sa.select(
            _customers.c.id,
            _customers.c.company_name,
            _customers.c.first_name,
            _customers.c.last_name,
        )
        .where(~has_profile)
        .order_by(_customers.c.created_at, _customers.c.id)
    ).fetchall()
    if not rows:
        return 0

    persisted = bind.execute(
        sa.select(_sequences.c.last_value).where(
            _sequences.c.scope_key == PROFILE_CODE_SCOPE_KEY
        )
    ).scalar()
    suffix = persisted or 0

    now = datetime.now(UTC)
    inserts: list[dict] = []
    for row in rows:
        suffix += 1
        display_name = row.company_name or f"{row.first_name} {row.last_name}".strip()
        slug = profile_code_slug(display_name)
        for currency in BillingCurrency:
            values = default_profile_values(currency.value)
            inserts.append(
                {
                    "id": uuid.uuid4(),
                    "customer_id": row.id,
                    "currency": currency.value,
                    "code": build_profile_code(slug, suffix, currency.value),
                    "payment_terms": values["payment_terms"],
                    "tax_treatment": values["tax_treatment"],
                    "credit_limit": values["credit_limit"],
                    "synced": False,
                    "synced_at": None,
                    "version": 1,
                    "created_at": now,
                    "updated_at": now,
                }
            )

    bind.execute(_profiles.insert(), inserts)

    if persisted is None:
        bind.execute(
            _sequences.insert().values(
                scope_key=PROFILE_CODE_SCOPE_KEY, last_value=suffix
            )
        )
    else:
        bind.execute(
            _sequences.update()
            .where(_sequences.c.scope_key == PROFILE_CODE_SCOPE_KEY)
            .values(last_value=suffix)
        )

    return len(rows)
