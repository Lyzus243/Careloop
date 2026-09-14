import os
from dotenv import load_dotenv
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import event
from sqlalchemy.engine import Engine

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///careloop.db")

# Strip sslmode/channel_binding from the URL (asyncpg needs ssl passed as a separate arg,
# not as a query param) using real URL parsing so it works regardless of param order/count.
connect_args = {}
if DATABASE_URL.startswith("postgresql"):
    parts = urlsplit(DATABASE_URL)
    query_params = [
        (k, v) for k, v in parse_qsl(parts.query)
        if k not in ("sslmode", "channel_binding")
    ]
    DATABASE_URL = urlunsplit((
        parts.scheme, parts.netloc, parts.path,
        urlencode(query_params), parts.fragment
    ))
    connect_args = {"ssl": "require"}

engine = create_async_engine(DATABASE_URL, echo=True, connect_args=connect_args)

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

Base = declarative_base()

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
