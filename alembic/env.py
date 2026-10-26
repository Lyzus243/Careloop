import os
import sys
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

# Make the application package importable when alembic runs from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

# Importing the package registers every model on the shared metadata, which is
# what autogenerate compares the live database against.
from app.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    """Point alembic at the same database as the app, using a sync driver."""
    url = os.getenv("DATABASE_URL", "sqlite:///careloop.db")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url.replace("+asyncpg", "").replace("+aiosqlite", "")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    section = config.get_section(config.config_ini_section, {})
    # Escaped because ConfigParser treats a bare % as interpolation syntax.
    section["sqlalchemy.url"] = get_url().replace("%", "%%")

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    try:
        with connectable.connect() as connection:
            # Keep the migration lock and all schema/version changes on one
            # connection and transaction, including with transaction poolers.
            with connection.begin():
                if connection.dialect.name == "postgresql":
                    connection.exec_driver_sql("SET LOCAL lock_timeout = '120s'")
                    connection.exec_driver_sql("SELECT pg_advisory_xact_lock(726384120195)")
                elif connection.dialect.name == "sqlite":
                    connection.exec_driver_sql("PRAGMA busy_timeout = 120000")
                    connection.exec_driver_sql("BEGIN IMMEDIATE")
                context.configure(
                    connection=connection,
                    target_metadata=target_metadata,
                    compare_type=True,
                    render_as_batch=connection.dialect.name == "sqlite",
                )
                with context.begin_transaction():
                    context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
