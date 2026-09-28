import os
from dotenv import load_dotenv
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.models.base import Base

load_dotenv()

# libpq understands these; asyncpg raises TypeError on them. Neon, Supabase and
# Heroku all hand out connection strings containing sslmode at minimum.
_LIBPQ_ONLY_PARAMS = {"sslmode", "channel_binding", "gssencmode"}
_SSL_DISABLED = {"disable", "allow", "prefer"}


def _is_pooler(netloc: str) -> bool:
    """Neon and Supabase put connection pooling behind PgBouncer on these hosts."""
    return "-pooler" in netloc or "pgbouncer" in netloc


def _prepare_url(url: str):
    """Make a pasted Postgres URL usable by asyncpg.

    Returns (url, connect_args). Managed providers hand out sync, libpq-flavoured
    URLs, so this swaps in the asyncpg driver, turns sslmode into asyncpg's ssl
    argument, drops the libpq-only parameters, and disables prepared-statement
    caching when the host is a PgBouncer pooler (which cannot reuse named
    prepared statements).
    """
    # A bare sqlite:// URL is the form alembic wants; the app needs the async
    # driver, so accept either spelling rather than failing at import.
    if url.startswith("sqlite://"):
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1), {}

    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    if not url.startswith("postgresql+asyncpg://"):
        return url, {}

    parts = urlsplit(url)
    params, want_ssl = [], False
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key == "sslmode":
            want_ssl = value not in _SSL_DISABLED
        elif key in _LIBPQ_ONLY_PARAMS:
            continue
        else:
            params.append((key, value))

    present = {k for k, _ in params}
    connect_args = {}
    if want_ssl and "ssl" not in present:
        connect_args["ssl"] = "require"
    if _is_pooler(parts.netloc):
        if "prepared_statement_cache_size" not in present:
            params.append(("prepared_statement_cache_size", "0"))
        # asyncpg's own cache size has to be an int, so it cannot ride in the
        # query string the way the dialect-level setting above can.
        connect_args["statement_cache_size"] = 0

    url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment))
    return url, connect_args


# SQLite is only a local-development default. Container filesystems are
# ephemeral, so any real deployment must set DATABASE_URL.
DATABASE_URL, connect_args = _prepare_url(os.getenv("DATABASE_URL", "sqlite+aiosqlite:///careloop.db"))
SQL_ECHO = os.getenv("SQL_ECHO", "false").lower() in ("1", "true", "yes")

engine = create_async_engine(
    DATABASE_URL,
    echo=SQL_ECHO,
    connect_args=connect_args,
    pool_pre_ping=True,   # test each connection before using it; reconnects if Neon closed it
    pool_recycle=300,     # recycle connections every 5 minutes so they never go stale
)

# This pragma listener only applies when actually using SQLite (e.g. local fallback)
if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(Engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("Database initialized successfully")
    except Exception as e:
        print(f"Database initialization error: {e}")
        raise e
