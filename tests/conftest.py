"""Test fixtures: an in-memory database and an authenticated API client.

The ASGI transport is used without lifespan, so the background send loop and the
other startup tasks never run during tests.
"""
import os
import asyncio

# Configure the integration before any app module is imported.
os.environ.setdefault("SECRET_KEY", "test_secret_key_do_not_use_in_production")
os.environ.setdefault("META_APP_ID", "111111111")
os.environ.setdefault("META_APP_SECRET", "test_app_secret")
os.environ.setdefault("META_CONFIG_ID", "222222222")
os.environ.setdefault("META_GRAPH_VERSION", "v21.0")
os.environ.setdefault("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "test_verify_token")
os.environ.setdefault("WHATSAPP_PHONE_PIN", "123456")

from cryptography.fernet import Fernet
os.environ.setdefault("WHATSAPP_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())

import pytest
import pytest_asyncio
import httpx
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.models.base import Base
import app.models  # noqa: F401  (registers every table)
from app.models.user import User
from app.models.customer import Customer
from app.models.whatsapp_account import WhatsAppAccount, ACCOUNT_CONNECTED
from app.utils import crypto


TEST_USER_ID = 1


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def db(session_factory):
    async with session_factory() as s:
        yield s


@pytest_asyncio.fixture
async def user(db):
    u = User(
        id=TEST_USER_ID,
        email="vendor@example.com",
        full_name="Vendor Owner",
        business_name="Bella's Boutique",
        phone="+2348030000000",
        hashed_password="x",
        is_active=True,
        is_email_verified=True,
    )
    db.add(u)
    await db.commit()
    return u


@pytest_asyncio.fixture
async def account(db, user):
    acc = WhatsAppAccount(
        user_id=user.id,
        waba_id="WABA123",
        phone_number_id="PHONE456",
        display_phone_number="+234 803 000 0000",
        verified_name="Bella's Boutique",
        access_token_encrypted=crypto.encrypt("test-token"),
        status=ACCOUNT_CONNECTED,
        opener_template_status="APPROVED",
        app_subscribed=True,
        phone_registered=True,
    )
    db.add(acc)
    await db.commit()
    return acc


@pytest.fixture(autouse=True)
def disable_rate_limiter():
    from app.rate_limit import limiter
    limiter.enabled = False
    yield
    limiter.enabled = True


@pytest_asyncio.fixture
async def other_user(db):
    u = User(
        id=999,
        email="other@example.com",
        full_name="Other Owner",
        business_name="Other Shop",
        hashed_password="x",
        is_active=True,
        is_email_verified=True,
    )
    db.add(u)
    await db.commit()
    return u


@pytest_asyncio.fixture
async def client(session_factory, user):
    """Authenticated client bound to the in-memory database."""
    from app.main import app
    from app.database import get_db
    from app.dependencies import get_current_user_id

    async def override_get_db():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user_id] = lambda: user.id

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def make_customer(db, user):
    async def _make(name, phone, **kwargs):
        c = Customer(user_id=user.id, name=name, phone_number=phone, **kwargs)
        db.add(c)
        await db.commit()
        await db.refresh(c)
        return c
    return _make
