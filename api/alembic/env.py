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
# substitution. A percent-encoded character in the password — `%40` for `@`, `%23`
# for `#`, anything urlencode produces — then blows up with "invalid interpolation
# syntax" before a single migration runs. Doubling the sign is configparser's own
# escape and leaves URLs without one untouched.
config.set_main_option("sqlalchemy.url", str(settings.DATABASE_URL).replace("%", "%%"))

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
