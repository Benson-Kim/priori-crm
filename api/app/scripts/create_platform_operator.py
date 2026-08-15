"""Idempotent seed script for the platform-operator account (issue #62).

ADR-0011 made module entitlements operator-granted: every entitlement write
requires a ``platform_operator`` user, a role no registration or
user-management flow can produce. This script is the deliberate, out-of-band
bootstrap path — it runs only with direct database access and is NEVER
exposed through any API (QA finding 09: no API-reachable self-promotion).

Usage::

    PLATFORM_OPERATOR_EMAIL=ops@example.com \
    PLATFORM_OPERATOR_PASSWORD=... \
        python -m app.scripts.create_platform_operator

    # or interactively (prompts; the password is read hidden, never echoed)
    python -m app.scripts.create_platform_operator

    # promote an EXISTING tenant user (explicit opt-in, refused otherwise)
    python -m app.scripts.create_platform_operator --force-role

Security posture:

- Credentials are read from environment variables or an interactive prompt,
  NEVER from CLI arguments (argv lands in shell history and process lists).
- The password is hashed with :func:`app.common.security.hash_password`
  (bcrypt) and is never printed, logged, or stored in plaintext.
- Re-running with the same email updates that one row (password rotation /
  break-glass recovery); it never creates a duplicate.
- Changing an existing tenant user's role requires ``--force-role``.
- Unlike ``scripts.seed_user`` this script is expected to run in production
  — it is the only way a production deployment obtains an operator.

Exit codes: 0 on success; 1 on invalid input or a refused role change;
2 when the database configuration is missing or the database is unreachable.
"""

import argparse
import getpass
import logging
import os
import sys
from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.modules.auth.models import User

logger = logging.getLogger(__name__)

ENV_EMAIL = "PLATFORM_OPERATOR_EMAIL"
ENV_PASSWORD = "PLATFORM_OPERATOR_PASSWORD"  # noqa: S105 — env var *name*, not a secret

EXIT_USAGE = 1
EXIT_INFRA = 2


class RoleConflictError(Exception):
    """An existing user holds a tenant role and ``--force-role`` was not given."""


def _fail(message: str, code: int = EXIT_USAGE) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def _normalize_email(email: str) -> str:
    """Trim and lowercase the domain part, mirroring EmailStr at login."""
    local, _, domain = email.strip().rpartition("@")
    if not local or not domain or "." not in domain:
        raise ValueError(f"{email.strip()!r} is not a valid email address.")
    return f"{local}@{domain.lower()}"


def upsert_platform_operator(
    db: "Session",
    email: str,
    password: str,
    *,
    force_role: bool = False,
) -> tuple["User", str]:
    """Create or update the one ``users`` row for ``email`` as an operator.

    Returns ``(user, action)`` where action is ``"created"`` (no row
    existed), ``"updated"`` (the row was already an operator; the password
    is rotated and the account reactivated — the break-glass path), or
    ``"promoted"`` (a tenant-role row was converted with ``force_role``).

    Raises :class:`RoleConflictError` when the email belongs to an existing
    tenant user and ``force_role`` is False, and :class:`ValueError` for an
    invalid email or a password failing the shared account policy. Nothing
    is written in either failure case. The caller owns the commit.
    """
    # App imports are local so importing this module never requires a loaded
    # configuration (main() translates config errors into clean exits).
    from app.common.security import hash_password
    from app.common.validators import validate_password_strength
    from app.constants.enums import UserRole
    from app.modules.auth.models import User

    email = _normalize_email(email)
    # Enforce the shared policy up front: LoginRequest validates it too, so
    # a weaker password would seed an account that can never sign in.
    validate_password_strength(password)

    user = db.query(User).filter(User.email == email).first()
    if user is None:
        user = User(
            email=email,
            password_hash=hash_password(password),
            first_name="Platform",
            last_name="Operator",
            role=UserRole.PLATFORM_OPERATOR.value,
            is_active=True,
        )
        db.add(user)
        db.flush()
        return user, "created"

    if user.role != UserRole.PLATFORM_OPERATOR.value:
        if not force_role:
            raise RoleConflictError(
                f"A user with email {email!r} already exists with tenant "
                f"role {user.role!r}. Refusing to change an existing tenant "
                "user's role; re-run with --force-role to promote this "
                "account to platform_operator. Nothing was written."
            )
        action = "promoted"
    else:
        action = "updated"

    user.role = UserRole.PLATFORM_OPERATOR.value
    user.password_hash = hash_password(password)
    user.is_active = True
    db.flush()
    return user, action


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.scripts.create_platform_operator",
        description=(
            "Idempotently create (or rotate the credentials of) the "
            "platform-operator account (ADR-0011)."
        ),
        epilog=(
            f"Credentials are read from {ENV_EMAIL} / {ENV_PASSWORD} or "
            "prompted interactively. They are deliberately NOT accepted as "
            "CLI arguments (shell history / process-list exposure)."
        ),
    )
    parser.add_argument(
        "--force-role",
        action="store_true",
        help=(
            "Allow promoting an EXISTING tenant user (admin/manager/member) "
            "to platform_operator. Without this flag the script refuses to "
            "change any existing user's role."
        ),
    )
    return parser.parse_args(argv)


def _gather_credentials() -> tuple[str, str]:
    """Read email + password from the environment or an interactive prompt."""
    email = os.environ.get(ENV_EMAIL, "").strip()
    password = os.environ.get(ENV_PASSWORD, "")
    interactive = sys.stdin.isatty()

    if not email:
        if not interactive:
            _fail(
                f"{ENV_EMAIL} is not set and no interactive terminal is "
                "attached. Set the environment variable or run the script "
                "in a terminal. Nothing was written."
            )
        email = input("Platform operator email: ").strip()

    if not password:
        if not interactive:
            _fail(
                f"{ENV_PASSWORD} is not set and no interactive terminal is "
                "attached. Set the environment variable or run the script "
                "in a terminal. Nothing was written."
            )
        password = getpass.getpass("Platform operator password (input hidden): ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            _fail("Passwords do not match. Nothing was written.")

    if not email or not password:
        _fail("Both an email and a password are required. Nothing was written.")
    return email, password


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    email, password = _gather_credentials()

    # Importing the database module loads and validates the application
    # configuration; a missing/invalid DATABASE_URL surfaces here as a
    # clean non-zero exit instead of a pydantic traceback.
    try:
        from app.common.database import check_database_connection, get_db_context
    except Exception as exc:  # pydantic ValidationError at settings import
        _fail(
            "Cannot load the database configuration — is DATABASE_URL set "
            f"and valid? Underlying error: {exc}",
            EXIT_INFRA,
        )

    from sqlalchemy.exc import DBAPIError

    if not check_database_connection():
        _fail(
            "The database at DATABASE_URL is unreachable. Verify the DSN, "
            "network access and credentials, then re-run. Nothing was "
            "written.",
            EXIT_INFRA,
        )

    try:
        with get_db_context() as db:
            user, action = upsert_platform_operator(
                db, email, password, force_role=args.force_role
            )
            user_id, role = user.id, user.role
    except RoleConflictError as exc:
        _fail(str(exc))
    except ValueError as exc:
        _fail(f"Invalid input: {exc} Nothing was written.")
    except DBAPIError:
        _fail(
            "A database error occurred while writing the operator row; the "
            "transaction was rolled back. See the log output above.",
            EXIT_INFRA,
        )

    # Deliberately terse: id + role only — never the password, never a hash.
    logger.info("Platform operator %s: id=%s role=%s", action, user_id, role)


if __name__ == "__main__":
    main()
