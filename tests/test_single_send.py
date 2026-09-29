"""The message modal's one-off send, which follows the same window rules."""
from datetime import datetime, timedelta

import pytest
import respx
import httpx
from sqlalchemy import select

from app.services.whatsapp_service import whatsapp_service
from app.models.whatsapp_contact import WhatsAppContact
from app.models.whatsapp_message import WhatsAppMessage, MSG_SENT, MSG_AWAITING_REPLY

MESSAGES_URL = f"{whatsapp_service.base_url}/PHONE456/messages"


def ok():
    return httpx.Response(200, json={"messages": [{"id": "wamid.ONE"}]})


@respx.mock
async def test_in_window_sends_text_directly(client, db, user, account, make_customer):
    c = await make_customer("Ada", "+2348031234567")
    db.add(WhatsAppContact(
        user_id=user.id, phone_e164="2348031234567",
        last_inbound_at=datetime.utcnow() - timedelta(hours=1),
    ))
    await db.commit()
    route = respx.post(MESSAGES_URL).mock(return_value=ok())

    r = await client.post("/api/whatsapp/send",
                          json={"customer_id": c.id, "text_body": "Your order is ready"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == MSG_SENT
    body = route.calls[0].request.content.decode()
    assert '"type": "text"' in body or '"type":"text"' in body


@respx.mock
async def test_outside_window_sends_opener_and_parks(client, db, user, account, make_customer):
    c = await make_customer("Ada", "+2348031234567")
    route = respx.post(MESSAGES_URL).mock(return_value=ok())

    r = await client.post("/api/whatsapp/send",
                          json={"customer_id": c.id, "text_body": "Weekend sale"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == MSG_AWAITING_REPLY

    body = route.calls[0].request.content.decode()
    assert "template" in body
    assert "careloop_message_opener" in body

    msg = (await db.execute(
        select(WhatsAppMessage).where(WhatsAppMessage.customer_id == c.id)
    )).scalar_one()
    assert msg.body == "Weekend sale"    # parked for after the reply
    assert msg.expires_at is not None


async def test_rejects_customer_without_international_number(client, db, account, make_customer):
    c = await make_customer("Chidi", "08031234567")
    r = await client.post("/api/whatsapp/send",
                          json={"customer_id": c.id, "text_body": "Hi"})
    assert r.status_code == 400
    assert "international" in r.json()["detail"].lower()


async def test_rejects_opted_out_customer(client, db, user, account, make_customer):
    c = await make_customer("Dele", "+2348034444444")
    db.add(WhatsAppContact(
        user_id=user.id, phone_e164="2348034444444", opted_out=True,
    ))
    await db.commit()

    r = await client.post("/api/whatsapp/send",
                          json={"customer_id": c.id, "text_body": "Hi"})
    assert r.status_code == 409


async def test_rejects_other_users_customer(client, db, other_user, account):
    from app.models.customer import Customer
    foreign = Customer(user_id=other_user.id, name="Stranger", phone_number="+2348039999999")
    db.add(foreign)
    await db.commit()
    await db.refresh(foreign)

    r = await client.post("/api/whatsapp/send",
                          json={"customer_id": foreign.id, "text_body": "Hi"})
    assert r.status_code == 404


async def test_status_endpoint_reports_disconnected(client, db, user):
    r = await client.get("/api/whatsapp/status")
    assert r.status_code == 200
    assert r.json()["connected"] is False


async def test_status_endpoint_never_leaks_token(client, account):
    r = await client.get("/api/whatsapp/status")
    assert "access_token" not in r.text
    assert "test-token" not in r.text
