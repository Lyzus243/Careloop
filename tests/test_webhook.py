import json
import hmac
import hashlib
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.services.whatsapp_service import whatsapp_service
from app.models.whatsapp_message import (
    WhatsAppMessage, MSG_SENT, MSG_DELIVERED, MSG_READ, MSG_FAILED,
    MSG_AWAITING_REPLY, MSG_REPLIED, MSG_QUEUED, MSG_TEXT, MSG_OPENER,
)
from app.models.whatsapp_contact import WhatsAppContact
from app.models.whatsapp_template import WhatsAppTemplate


def sign(body: bytes) -> str:
    digest = hmac.new(
        whatsapp_service.app_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return f"sha256={digest}"


async def post_webhook(client, payload, signature=None):
    raw = json.dumps(payload).encode()
    headers = {
        "X-Hub-Signature-256": signature if signature is not None else sign(raw),
        "Content-Type": "application/json",
    }
    return await client.post("/api/whatsapp/webhook", content=raw, headers=headers)


def status_payload(waba_id, wamid, status, errors=None, ts=None):
    entry = {
        "id": waba_id,
        "changes": [
            {
                "field": "messages",
                "value": {
                    "metadata": {"phone_number_id": "PHONE456"},
                    "statuses": [
                        {
                            "id": wamid,
                            "status": status,
                            "timestamp": str(int((ts or datetime.utcnow()).timestamp())),
                            **({"errors": errors} if errors else {}),
                        }
                    ],
                },
            }
        ],
    }
    return {"object": "whatsapp_business_account", "entry": [entry]}


# --- verification (GET) ----------------------------------------------------

async def test_get_verification_returns_challenge(client):
    r = await client.get(
        "/api/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "test_verify_token",
            "hub.challenge": "abc123",
        },
    )
    assert r.status_code == 200
    assert r.text == "abc123"


async def test_get_verification_rejects_wrong_token(client):
    r = await client.get(
        "/api/whatsapp/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "x"},
    )
    assert r.status_code == 403


# --- signature -------------------------------------------------------------

async def test_rejects_missing_signature(client, account):
    raw = json.dumps({"object": "whatsapp_business_account", "entry": []}).encode()
    r = await client.post("/api/whatsapp/webhook", content=raw)
    assert r.status_code == 403


async def test_rejects_tampered_body(client, account):
    payload = status_payload("WABA123", "wamid.1", "delivered")
    raw = json.dumps(payload).encode()
    good = sign(raw)
    tampered = json.dumps({**payload, "extra": "injected"}).encode()
    r = await client.post(
        "/api/whatsapp/webhook",
        content=tampered,
        headers={"X-Hub-Signature-256": good},
    )
    assert r.status_code == 403


# --- delivery status -------------------------------------------------------

async def test_delivered_then_read_advances_status(client, db, account):
    msg = WhatsAppMessage(
        user_id=account.user_id, to_phone="2348031234567", kind=MSG_TEXT,
        status=MSG_SENT, wa_message_id="wamid.A",
    )
    db.add(msg)
    await db.commit()

    assert (await post_webhook(client, status_payload("WABA123", "wamid.A", "delivered"))).status_code == 200
    await db.refresh(msg)
    assert msg.status == MSG_DELIVERED

    assert (await post_webhook(client, status_payload("WABA123", "wamid.A", "read"))).status_code == 200
    await db.refresh(msg)
    assert msg.status == MSG_READ


async def test_redelivered_lower_status_does_not_downgrade(client, db, account):
    msg = WhatsAppMessage(
        user_id=account.user_id, to_phone="2348031234567", kind=MSG_TEXT,
        status=MSG_READ, wa_message_id="wamid.B",
    )
    db.add(msg)
    await db.commit()

    await post_webhook(client, status_payload("WABA123", "wamid.B", "delivered"))
    await db.refresh(msg)
    assert msg.status == MSG_READ   # still read, never walked backwards


async def test_failed_records_error(client, db, account):
    msg = WhatsAppMessage(
        user_id=account.user_id, to_phone="2348031234567", kind=MSG_TEXT,
        status=MSG_SENT, wa_message_id="wamid.C",
    )
    db.add(msg)
    await db.commit()

    errors = [{"code": 131047, "title": "Re-engagement message",
               "error_data": {"details": "Message failed to send because more than 24 hours have passed."}}]
    await post_webhook(client, status_payload("WABA123", "wamid.C", "failed", errors=errors))
    await db.refresh(msg)
    assert msg.status == MSG_FAILED
    assert msg.error_code == 131047
    assert "24 hours" in msg.error_message


async def test_unknown_wamid_is_ignored(client, account):
    r = await post_webhook(client, status_payload("WABA123", "wamid.unknown", "delivered"))
    assert r.status_code == 200


async def test_unknown_waba_is_ignored(client, account):
    r = await post_webhook(client, status_payload("OTHER_WABA", "wamid.x", "delivered"))
    assert r.status_code == 200


# --- inbound ---------------------------------------------------------------

def inbound_payload(waba_id, frm, body, ts=None):
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": waba_id,
            "changes": [{
                "field": "messages",
                "value": {
                    "metadata": {"phone_number_id": "PHONE456"},
                    "messages": [{
                        "from": frm,
                        "id": "wamid.in1",
                        "timestamp": str(int((ts or datetime.utcnow()).timestamp())),
                        "type": "text",
                        "text": {"body": body},
                    }],
                },
            }],
        }],
    }


async def test_inbound_records_window_and_links_customer(client, db, account, make_customer):
    customer = await make_customer("Ada", "+2348031234567")
    r = await post_webhook(client, inbound_payload("WABA123", "2348031234567", "Hello"))
    assert r.status_code == 200

    contact = (await db.execute(
        select(WhatsAppContact).where(WhatsAppContact.phone_e164 == "2348031234567")
    )).scalar_one()
    assert contact.last_inbound_at is not None
    assert contact.in_service_window() is True
    assert contact.customer_id == customer.id
    assert contact.opted_out is False


async def test_stop_reply_opts_out(client, db, account, make_customer):
    await make_customer("Ada", "+2348031234567")
    await post_webhook(client, inbound_payload("WABA123", "2348031234567", "STOP"))

    contact = (await db.execute(
        select(WhatsAppContact).where(WhatsAppContact.phone_e164 == "2348031234567")
    )).scalar_one()
    assert contact.opted_out is True
    assert contact.opt_out_source == "inbound_stop"


async def test_start_reply_clears_opt_out(client, db, account, make_customer):
    await make_customer("Ada", "+2348031234567")
    await post_webhook(client, inbound_payload("WABA123", "2348031234567", "STOP"))
    await post_webhook(client, inbound_payload("WABA123", "2348031234567", "START"))

    contact = (await db.execute(
        select(WhatsAppContact).where(WhatsAppContact.phone_e164 == "2348031234567")
    )).scalar_one()
    assert contact.opted_out is False


# --- template status -------------------------------------------------------

async def test_template_status_update_flips_opener(client, db, account):
    account.opener_template_status = "PENDING"
    await db.commit()

    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA123",
            "changes": [{
                "field": "message_template_status_update",
                "value": {
                    "event": "APPROVED",
                    "message_template_name": "careloop_message_opener",
                    "message_template_language": "en",
                },
            }],
        }],
    }
    await post_webhook(client, payload)
    await db.refresh(account)
    assert account.opener_template_status == "APPROVED"


async def test_inbound_matches_customer_with_spaced_number(client, db, account, make_customer):
    """Customer numbers are free text; separators must not break matching."""
    customer = await make_customer("Ada", "+234 803 123 4567")
    await post_webhook(client, inbound_payload("WABA123", "2348031234567", "Hi"))

    contact = (await db.execute(
        select(WhatsAppContact).where(WhatsAppContact.phone_e164 == "2348031234567")
    )).scalar_one()
    assert contact.customer_id == customer.id


async def test_inbound_from_unknown_number_still_records_window(client, db, account):
    await post_webhook(client, inbound_payload("WABA123", "2348037777777", "Hi"))
    contact = (await db.execute(
        select(WhatsAppContact).where(WhatsAppContact.phone_e164 == "2348037777777")
    )).scalar_one()
    assert contact.customer_id is None
    assert contact.in_service_window() is True


async def test_account_locked_error_flags_the_whole_account(client, db, account):
    """Error 131031 means every send will fail, so the account must be flagged.

    Seen live: an unverified business tried a billable marketing message and
    Meta reported 'Business Account locked' on the individual message.
    """
    from app.models.whatsapp_account import ACCOUNT_ERROR
    msg = WhatsAppMessage(
        user_id=account.user_id, to_phone="2348143648991", kind=MSG_TEXT,
        status=MSG_SENT, wa_message_id="wamid.LOCKED",
    )
    db.add(msg)
    await db.commit()

    errors = [{"code": 131031, "title": "Business Account locked",
               "error_data": {"details": "Business account has been locked."}}]
    r = await post_webhook(client, status_payload("WABA123", "wamid.LOCKED", "failed", errors=errors))
    assert r.status_code == 200

    await db.refresh(msg)
    await db.refresh(account)
    assert msg.status == MSG_FAILED
    assert msg.error_code == 131031
    assert account.status == ACCOUNT_ERROR
    assert "restricted" in account.last_error
    assert "verification" in account.last_error


async def test_ordinary_failure_does_not_flag_the_account(client, db, account):
    """A per-recipient failure must not mark the whole account unhealthy."""
    from app.models.whatsapp_account import ACCOUNT_CONNECTED
    msg = WhatsAppMessage(
        user_id=account.user_id, to_phone="2348143648991", kind=MSG_TEXT,
        status=MSG_SENT, wa_message_id="wamid.ONEOFF",
    )
    db.add(msg)
    await db.commit()

    errors = [{"code": 131026, "title": "Message undeliverable"}]
    await post_webhook(client, status_payload("WABA123", "wamid.ONEOFF", "failed", errors=errors))

    await db.refresh(account)
    assert account.status == ACCOUNT_CONNECTED
