"""The opener + reply flow: a parked custom message is delivered once the
customer replies, which is what makes custom bulk messaging possible at all."""
import json
import hmac
import hashlib
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.services.whatsapp_service import whatsapp_service
from app.models.whatsapp_message import (
    WhatsAppMessage, MSG_AWAITING_REPLY, MSG_REPLIED, MSG_QUEUED,
    MSG_EXPIRED, MSG_CANCELLED, MSG_TEXT, MSG_OPENER,
)
from app.models.whatsapp_contact import WhatsAppContact


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(
        whatsapp_service.app_secret.encode(), body, hashlib.sha256
    ).hexdigest()


async def send_inbound(client, frm, body="YES"):
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA123",
            "changes": [{
                "field": "messages",
                "value": {
                    "metadata": {"phone_number_id": "PHONE456"},
                    "messages": [{
                        "from": frm,
                        "id": "wamid.in",
                        "timestamp": str(int(datetime.utcnow().timestamp())),
                        "type": "text",
                        "text": {"body": body},
                    }],
                },
            }],
        }],
    }
    raw = json.dumps(payload).encode()
    return await client.post(
        "/api/whatsapp/webhook", content=raw,
        headers={"X-Hub-Signature-256": sign(raw), "Content-Type": "application/json"},
    )


async def make_parked(db, account, phone="2348031234567", body="Big sale this weekend!", expires_in_days=7):
    opener = WhatsAppMessage(
        user_id=account.user_id,
        customer_name="Ada",
        to_phone=phone,
        kind=MSG_OPENER,
        body=body,
        status=MSG_AWAITING_REPLY,
        wa_message_id="wamid.opener1",
        sent_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(days=expires_in_days),
    )
    db.add(opener)
    await db.commit()
    await db.refresh(opener)
    return opener


async def test_reply_releases_parked_message(client, db, account):
    opener = await make_parked(db, account)

    r = await send_inbound(client, "2348031234567", "YES")
    assert r.status_code == 200

    await db.refresh(opener)
    assert opener.status == MSG_REPLIED
    assert opener.follow_up_message_id is not None

    follow_up = (await db.execute(
        select(WhatsAppMessage).where(WhatsAppMessage.id == opener.follow_up_message_id)
    )).scalar_one()
    assert follow_up.kind == MSG_TEXT
    assert follow_up.status == MSG_QUEUED
    assert follow_up.body == "Big sale this weekend!"
    assert follow_up.to_phone == "2348031234567"


async def test_stop_reply_does_not_release_and_cancels(client, db, account):
    opener = await make_parked(db, account)

    await send_inbound(client, "2348031234567", "STOP")

    await db.refresh(opener)
    assert opener.status == MSG_CANCELLED
    assert opener.follow_up_message_id is None

    texts = (await db.execute(
        select(WhatsAppMessage).where(WhatsAppMessage.kind == MSG_TEXT)
    )).scalars().all()
    assert texts == []

    contact = (await db.execute(
        select(WhatsAppContact).where(WhatsAppContact.phone_e164 == "2348031234567")
    )).scalar_one()
    assert contact.opted_out is True


async def test_reply_after_expiry_does_not_deliver(client, db, account):
    opener = await make_parked(db, account, expires_in_days=-1)  # already expired

    await send_inbound(client, "2348031234567", "YES")

    await db.refresh(opener)
    assert opener.status == MSG_EXPIRED
    assert opener.follow_up_message_id is None


async def test_reply_from_other_number_leaves_parked_message(client, db, account):
    opener = await make_parked(db, account, phone="2348031234567")

    await send_inbound(client, "2348039999999", "YES")

    await db.refresh(opener)
    assert opener.status == MSG_AWAITING_REPLY


async def test_expire_openers_marks_stale_rows(db, account):
    from app.services.whatsapp_worker import expire_openers
    await make_parked(db, account, expires_in_days=-2)
    n = await expire_openers(db)
    assert n == 1
