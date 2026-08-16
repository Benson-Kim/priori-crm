def get_or_create_baseline(db: Session, user_id) -> "UserBaseline":  # noqa: F821
    """Load the user's baseline row, creating an empty one on first sight.

    Idempotent upsert semantics (#67 review H14/L3): two concurrent
    first-scored requests can both observe "no row" and both INSERT — the
    loser's primary-key violation must not surface as a 500. The INSERT
    runs inside a SAVEPOINT so an IntegrityError rolls back only the
    attempted insert (never the surrounding request transaction), and the
    winner's committed row is re-fetched instead.
    """
    from app.modules.auth.models import UserBaseline

    baseline = db.get(UserBaseline, user_id)
    if baseline is not None:
        return baseline

    try:
        with db.begin_nested():
            baseline = UserBaseline(
                user_id=user_id,
                known_devices=[],
                known_countries=[],
                hour_counts={},
                hour_observations=0,
                volume_baselines={},
            )
            db.add(baseline)
            db.flush()
    except IntegrityError:
        # A concurrent request won the first-INSERT race; use its row.
        baseline = db.get(UserBaseline, user_id)
        if baseline is None:  # pragma: no cover — the winner's row exists
            raise
    return baseline