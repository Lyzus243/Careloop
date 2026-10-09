"""Sessions: login issues a refresh token that keeps the user signed in.

    pytest tests/test_auth_session.py
"""
import asyncio
import os
from datetime import timedelta

os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-bytes-long")

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.controllers.auth_controller import AuthController
from app.dependencies import get_current_user_id
from app.models.base import Base
from app.models.user import User
from app.schemas.user import ChangePasswordRequest, UserLogin
from app.services.auth_service import PasswordService, TokenService

PASSWORD = "Secret123"


@pytest.fixture
def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return Session()

    session = run(setup())
    yield session
    run(session.close())
    run(engine.dispose())


def run(coro):
    return asyncio.get_event_loop_policy().get_event_loop().run_until_complete(coro)


async def login(db, email="owner@example.com"):
    db.add(User(email=email, full_name="Ada Owner", is_email_verified=True,
                hashed_password=PasswordService.hash_password(PASSWORD)))
    await db.commit()
    return await AuthController.authenticate_user(db, UserLogin(email=email, password=PASSWORD))


async def user_id_for(db, token):
    return await get_current_user_id(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), db)


async def refused(coro):
    with pytest.raises(HTTPException) as exc:
        await coro
    return exc.value.status_code == 401


def test_refresh_gives_a_working_access_token_and_rotates(db):
    async def go():
        first = await login(db)
        assert first.refresh_token

        second = await AuthController.refresh_session(db, first.refresh_token)
        assert await user_id_for(db, second.access_token) == 1
        assert second.refresh_token != first.refresh_token

        # A used refresh token can't be replayed, but its replacement works.
        assert await refused(AuthController.refresh_session(db, first.refresh_token))
        assert (await AuthController.refresh_session(db, second.refresh_token)).access_token
    run(go())


def test_refresh_token_is_not_an_access_token(db):
    async def go():
        session = await login(db)
        assert await refused(user_id_for(db, session.refresh_token))
        assert await refused(AuthController.refresh_session(db, session.access_token))
    run(go())


def test_expired_refresh_token_is_refused(db):
    async def go():
        await login(db)
        expired = TokenService.create_access_token(
            {"sub": "1", "type": "refresh", "pwd": "x"}, expires_delta=timedelta(seconds=-1))
        assert await refused(AuthController.refresh_session(db, expired))
    run(go())


def test_changing_password_signs_out_other_devices_but_not_this_one(db):
    async def go():
        other_device = await login(db)
        this_device = await AuthController.refresh_session(
            db, (await AuthController.authenticate_user(
                db, UserLogin(email="owner@example.com", password=PASSWORD))).refresh_token)

        changed = await AuthController.change_password(
            db, 1, ChangePasswordRequest(current_password=PASSWORD, new_password="Newpass456"))

        assert await refused(AuthController.refresh_session(db, other_device.refresh_token))
        assert await refused(AuthController.refresh_session(db, this_device.refresh_token))
        assert (await AuthController.refresh_session(db, changed.refresh_token)).access_token
    run(go())


def test_deactivated_user_cannot_refresh(db):
    async def go():
        session = await login(db)
        user = await AuthController._get_user_by_id(db, 1)
        user.is_active = False
        await db.commit()
        assert await refused(AuthController.refresh_session(db, session.refresh_token))
    run(go())


def test_logout_revokes_both_tokens(db):
    async def go():
        session = await login(db)
        await AuthController.revoke_tokens(db, [session.access_token, session.refresh_token])
        assert await refused(user_id_for(db, session.access_token))
        assert await refused(AuthController.refresh_session(db, session.refresh_token))
        # Logging out twice is harmless.
        await AuthController.revoke_tokens(db, [session.access_token, session.refresh_token])
    run(go())
