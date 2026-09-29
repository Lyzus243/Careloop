"""Sending worker: success, retry/backoff, permanent failure and auth loss."""
from datetime import datetime, timedelta

import pytest
import respx
import httpx
from sqlalchemy import select

from app.services.whatsapp_service import whatsapp_service
from app.services import whatsapp_worker as worker
from app.models.whatsapp_message import (
    WhatsAppMessage, MSG_QUEUED, MSG_SENT, MSG_FAILED, MSG_AWAITING_REPLY,
    MSG_SENDING, MSG_TEXT, MSG_OPENER, MSG_TEMPLATE,
)
from app.models.whatsapp_campaign import WhatsAppCampaign, CAMPAIGN_QUEUED, CAMPAIGN_COMPLETED
from app.models.whatsapp_account import ACCOUNT_ERROR
from app.models.whatsapp_contact import WhatsAppContact

MESSAGES_URL = f"{whatsapp_service.base_url}/PHONE456/messages"


@pytest.fixture(autouse=True)
def patch_session(session_factory, monkeypatch):
    """Point the worker at the in-memory test database."""
    monkeypatch.setattr(worker, "AsyncSessionLocal", session_factory)
    yield


async def queue_message(db, account, kind=MSG_TEXT, body="Hello", campaign_id=None):
    m = WhatsAppMessage(
        campaign_id=campaign_id,
        user_id=account.user_id,
        customer_name="Ada",
        to_phone="2348031234567",
        kind=kind,
        body=body,
        status=MSG_QUEUED,
        next_attempt_at=datetime.utcnow(),
    )
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m


def ok_response():
    return httpx.Response(200, json={
        "messaging_product": "whatsapp",
        "contacts": [{"input": "2348031234567", "wa_id": "2348031234567"}],
        "messages": [{"id": "wamid.SENT1"}],
    })


def error_response(status, code, message="error", subcode=None):
    err = {"message": message, "code": code, "type": "OAuthException"}
    if subcode:
        err["error_subcode"] = subcode
    return httpx.Response(status, json={"error": err})


@respx.mock
async def test_successful_text_send(db, account, session_factory):
    msg = await queue_message(db, account)
    route = respx.post(MESSAGES_URL).mock(return_value=ok_response())

    async with session_factory() as s:
        ids = await worker.claim_batch(s)
    assert ids == [msg.id]
    await worker.process_batch(ids)

    await db.refresh(msg)
    assert route.called
    assert msg.status == MSG_SENT
    assert msg.wa_message_id == "wamid.SENT1"
    assert msg.sent_at is not None

    contact = (await db.execute(
        select(WhatsAppContact).where(WhatsAppContact.phone_e164 == "2348031234567")
    )).scalar_one()
    assert contact.last_outbound_at is not None


@respx.mock
async def test_opener_send_parks_message(db, account, session_factory):
    msg = await queue_message(db, account, kind=MSG_OPENER, body="Parked text")
    respx.post(MESSAGES_URL).mock(return_value=ok_response())

    async with session_factory() as s:
        ids = await worker.claim_batch(s)
    await worker.process_batch(ids)

    await db.refresh(msg)
    assert msg.status == MSG_AWAITING_REPLY
    assert msg.expires_at is not None
    assert msg.expires_at > datetime.utcnow() + timedelta(days=6)
    assert msg.body == "Parked text"   # still held for delivery after the reply


@respx.mock
async def test_rate_limit_is_retried_with_backoff(db, account, session_factory):
    msg = await queue_message(db, account)
    respx.post(MESSAGES_URL).mock(return_value=error_response(429, 130429, "Rate limit hit"))

    async with session_factory() as s:
        ids = await worker.claim_batch(s)
    await worker.process_batch(ids)

    await db.refresh(msg)
    assert msg.status == MSG_QUEUED
    assert msg.attempts == 1
    assert msg.next_attempt_at > datetime.utcnow()


@respx.mock
async def test_outside_window_fails_permanently(db, account, session_factory):
    msg = await queue_message(db, account)
    respx.post(MESSAGES_URL).mock(
        return_value=error_response(400, 131047, "Re-engagement message")
    )

    async with session_factory() as s:
        ids = await worker.claim_batch(s)
    await worker.process_batch(ids)

    await db.refresh(msg)
    assert msg.status == MSG_FAILED
    assert msg.error_code == 131047
    assert msg.attempts == 1   # not retried


@respx.mock
async def test_expired_token_marks_account_and_fails_queue(db, account, session_factory):
    first = await queue_message(db, account)
    second = await queue_message(db, account)
    respx.post(MESSAGES_URL).mock(
        return_value=error_response(401, 190, "Access token has expired")
    )

    async with session_factory() as s:
        ids = await worker.claim_batch(s, limit=1)
    await worker.process_batch(ids)

    await db.refresh(first)
    await db.refresh(second)
    await db.refresh(account)
    assert first.status == MSG_FAILED
    assert account.status == ACCOUNT_ERROR
    # The sibling still queued is failed too rather than retried forever.
    assert second.status == MSG_FAILED


@respx.mock
async def test_retries_stop_after_max_attempts(db, account, session_factory):
    msg = await queue_message(db, account)
    msg.attempts = worker.MAX_ATTEMPTS - 1
    await db.commit()
    respx.post(MESSAGES_URL).mock(return_value=error_response(500, 131000, "Generic error"))

    async with session_factory() as s:
        ids = await worker.claim_batch(s)
    await worker.process_batch(ids)

    await db.refresh(msg)
    assert msg.status == MSG_FAILED


async def test_claim_batch_is_idempotent(db, account, session_factory):
    await queue_message(db, account)
    async with session_factory() as s:
        first = await worker.claim_batch(s)
    async with session_factory() as s:
        second = await worker.claim_batch(s)
    assert len(first) == 1
    assert second == []   # already moved to 'sending'


async def test_reap_stuck_ignores_recent_in_flight_messages(db, account, session_factory):
    """A long batch keeps refreshing its claim and must not be reaped."""
    msg = await queue_message(db, account)
    msg.status = MSG_SENDING
    msg.updated_at = datetime.utcnow() - timedelta(minutes=2)
    await db.commit()

    async with session_factory() as s:
        assert await worker.reap_stuck(s) == 0
    await db.refresh(msg)
    assert msg.status == MSG_SENDING


async def test_reap_stuck_requeues_abandoned_messages(db, account, session_factory):
    msg = await queue_message(db, account)
    msg.status = MSG_SENDING
    msg.updated_at = datetime.utcnow() - timedelta(minutes=20)
    await db.commit()

    async with session_factory() as s:
        n = await worker.reap_stuck(s)
    assert n == 1
    await db.refresh(msg)
    assert msg.status == MSG_QUEUED


@respx.mock
async def test_finalize_campaign_completes_and_counts(db, account, session_factory):
    campaign = WhatsAppCampaign(
        user_id=account.user_id, account_id=account.id, kind="custom",
        audience="all", status=CAMPAIGN_QUEUED, total_recipients=1,
    )
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)

    await queue_message(db, account, campaign_id=campaign.id)
    respx.post(MESSAGES_URL).mock(return_value=ok_response())

    async with session_factory() as s:
        ids = await worker.claim_batch(s)
    await worker.process_batch(ids)
    async with session_factory() as s:
        await worker.finalize_campaigns(s)

    await db.refresh(campaign)
    assert campaign.status == CAMPAIGN_COMPLETED
    assert campaign.sent_count == 1
    assert campaign.completed_at is not None


@respx.mock
async def test_counters_keep_updating_after_campaign_completes(db, account, session_factory):
    """A custom campaign finishes sending openers, then replies keep arriving."""
    from app.models.whatsapp_message import MSG_REPLIED, MSG_AWAITING_REPLY

    campaign = WhatsAppCampaign(
        user_id=account.user_id, account_id=account.id, kind="custom",
        audience="all", status=CAMPAIGN_QUEUED, total_recipients=1,
    )
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)

    opener = await queue_message(db, account, kind=MSG_OPENER,
                                 body="Sale!", campaign_id=campaign.id)

    counter = {"n": 0}
    def unique_ok(request):
        counter["n"] += 1
        return httpx.Response(200, json={"messages": [{"id": f"wamid.U{counter['n']}"}]})
    respx.post(MESSAGES_URL).mock(side_effect=unique_ok)

    async with session_factory() as s:
        ids = await worker.claim_batch(s)
    await worker.process_batch(ids)
    async with session_factory() as s:
        await worker.finalize_campaigns(s)

    await db.refresh(campaign)
    assert campaign.status == CAMPAIGN_COMPLETED
    assert campaign.awaiting_count == 1

    # The customer replies: the parked text is queued and delivered.
    await db.refresh(opener)
    opener.status = MSG_REPLIED
    follow_up = WhatsAppMessage(
        campaign_id=campaign.id, user_id=account.user_id, to_phone="2348031234567",
        kind=MSG_TEXT, body="Sale!", status=MSG_QUEUED, next_attempt_at=datetime.utcnow(),
    )
    db.add(follow_up)
    await db.commit()

    async with session_factory() as s:
        ids = await worker.claim_batch(s)
    await worker.process_batch(ids)
    async with session_factory() as s:
        await worker.finalize_campaigns(s)

    await db.refresh(campaign)
    # Counters moved even though the campaign was already marked completed.
    assert campaign.replied_count == 1
    assert campaign.awaiting_count == 0
    assert campaign.sent_count == 1
