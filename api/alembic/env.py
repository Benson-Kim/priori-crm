import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.common.database import Base
from app.lib.config import settings

# Import all models so Base.metadata is populated
from app.modules.auth.models import OTPCode, User  # noqa: F401
from app.modules.customers.models import Customer  # noqa: F401
from app.modules.expenses.models import Expense  # noqa: F401
from app.modules.invoices.models import Invoice  # noqa: F401
from app.modules.purchase_orders.models import PurchaseOrder  # noqa: F401
from app.modules.quotes.models import Quote  # noqa: F401
from app.modules.vendors.models import Vendor  # noqa: F401

config = context.config
# alembic.ini is parsed by configparser with BasicInterpolation, where `%` starts a
# substitution. Do not go looking for the `%` in .env — it is not there. DATABASE_URL
# is a pydantic PostgresDsn, and str() on it percent-encodes special characters in
# the password, so a file holding a literal `<` produces `%3C` here. Measured on the
# staging host: the raw .env value is 81 chars with zero `%`; str(settings.DATABASE_URL)
# is 85 with two, at positions 32 and 45 — and configparser raised "invalid
# interpolation syntax ... at position 32" before a single migration ran.
#
# Doubling the sign is configparser's own escape. Both readers below interpolate on
# the way out (get_main_option offline, get_section online), so SQLAlchemy still
# receives the original URL; verified byte-identical against the real staging value.
#
# Role split (ADR-0013 Phase T1, issue #80): once the app_migrator/app_runtime
# split is live, migrations must run as app_migrator — the owner of every
# application table — so MIGRATOR_DATABASE_URL (read by pydantic-settings from
# the same .env / process environment as DATABASE_URL) takes precedence here.
# Unset, it falls back to DATABASE_URL, so single-role environments keep
# working unchanged. The API's request traffic never uses this URL.
_migration_url = settings.MIGRATOR_DATABASE_URL or settings.DATABASE_URL
config.set_main_option("sqlalchemy.url", str(_migration_url).replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

logger = logging.getLogger("alembic.env")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_schemas=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    logger.info("Running migrations in offline mode")
    run_migrations_offline()
else:
    logger.info("Running migrations in online mode")
    run_migrations_online()
