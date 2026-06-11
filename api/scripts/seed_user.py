"""Env-guarded management script for seeding a development user.

Plaintext-password seeding must never live in the production ``AuthService``,
so it is isolated here behind an explicit environment guard. The script
refuses to run when ``ENVIRONMENT == "production"``.

Usage::

    python -m scripts.seed_user --email frank@mail.com --password securepass1
    python -m scripts.seed_user  # uses SEED_USER_EMAIL / SEED_USER_PASSWORD env vars
"""

import argparse
import logging
import os
import sys

from app.common.database import get_db_context
from app.common.security import hash_password
from app.lib.config import settings
from app.modules.auth.models import User

logger = logging.getLogger(__name__)


def seed_user(
    email: str,
    password: str,
    first_name: str = "Frank",
    last_name: str = "Degods",
) -> None:
    """Create a user if one does not already exist. Development use only."""
    if settings.ENVIRONMENT == "production":
        raise SystemExit(
            "Refusing to seed a user: ENVIRONMENT is 'production'. "
            "This script is for development/staging only."
        )

    with get_db_context() as db:
        existing = db.query(User).filter(User.email == email).first()
        if existing is not None:
            logger.info("User already exists, nothing to do: %s", email)
            return

        user = User(
            email=email,
            password_hash=hash_password(password),
            first_name=first_name,
            last_name=last_name,
        )
        db.add(user)
        # get_db_context() owns the commit on successful exit.
        logger.info("Seeded user: %s", email)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed a development user.")
    parser.add_argument(
        "--email",
        default=os.getenv("SEED_USER_EMAIL"),
        help="Email for the seeded user (or set SEED_USER_EMAIL).",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("SEED_USER_PASSWORD"),
        help="Password for the seeded user (or set SEED_USER_PASSWORD).",
    )
    parser.add_argument("--first-name", default="Frank")
    parser.add_argument("--last-name", default="Degods")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO)
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    if not args.email or not args.password:
        raise SystemExit(
            "Both --email and --password (or SEED_USER_EMAIL / "
            "SEED_USER_PASSWORD) are required."
        )

    seed_user(
        email=args.email,
        password=args.password,
        first_name=args.first_name,
        last_name=args.last_name,
    )


if __name__ == "__main__":
    main()
