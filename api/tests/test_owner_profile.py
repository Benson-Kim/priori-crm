"""W3.6 - owner profile, immutable snapshots, render DTO."""

from app.modules.owner.schemas import OwnerProfileUpdate
from app.modules.owner.service import OwnerService


def test_get_or_create_seeds_singleton(db):
    svc = OwnerService(db)
    profile = svc.get_or_create()
    assert profile.full_name  # seeded from settings.APP_NAME
    # Idempotent: same singleton row returned.
    again = svc.get_or_create()
    assert again.id == profile.id


def test_update_changes_live_profile(db):
    svc = OwnerService(db)
    svc.get_or_create()
    updated = svc.update(
        OwnerProfileUpdate(
            full_name="Acme Ltd",
            email="BILLING@acme.example",
            phone="0712345678",
            website="acme.example",
        )
    )
    assert updated.full_name == "Acme Ltd"
    assert updated.email == "billing@acme.example"  # normalised lowercase
    assert updated.phone == "+254712345678"  # E.164 via shared validator
    assert updated.website == "https://acme.example"  # scheme prepended


def test_snapshot_is_immutable_against_later_profile_edits(db):
    """The locked product decision: issued headers never re-brand (V-DI-4)."""
    svc = OwnerService(db)
    svc.update(OwnerProfileUpdate(full_name="Original Name", address="Old Address"))

    snapshot = svc.snapshot_current()
    assert snapshot.full_name == "Original Name"
    assert snapshot.address == "Old Address"

    # Edit the live profile after the snapshot was taken.
    svc.update(OwnerProfileUpdate(full_name="Rebranded Name", address="New Address"))

    db.refresh(snapshot)
    assert snapshot.full_name == "Original Name"
    assert snapshot.address == "Old Address"
    # Live profile did change.
    assert svc.get_or_create().full_name == "Rebranded Name"


def test_unchanged_profile_reuses_snapshot_row(db):
    svc = OwnerService(db)
    svc.update(OwnerProfileUpdate(full_name="Stable Co"))
    first = svc.snapshot_current()
    second = svc.snapshot_current()
    assert first.id == second.id  # content-addressed reuse


def test_to_owner_info_falls_back_to_settings_when_none(db):
    info = OwnerService(db).to_owner_info(None)
    assert info.full_name  # settings.APP_NAME fallback
