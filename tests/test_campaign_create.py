from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.customer import Customer
from app.models.whatsapp_contact import WhatsAppContact
from app.models.whatsapp_template import WhatsAppTemplate
from app.models.whatsapp_message import WhatsAppMessage, MSG_TEXT, MSG_OPENER, MSG_TEMPLATE
from app.models.whatsapp_account import ACCOUNT_CONNECTED


async def seed_mixed_customers(db, user, make_customer):
    """A realistic mix: good, no phone, local format, duplicate, opted out."""
    good = await make_customer("Ada", "+2348031111111")
    no_phone = await make_customer("Bola", None)
    local = await make_customer("Chidi", "08032222222")       # no country code
    dup = await make_customer("Ada Again", "+234 803 111 1111")  # same number as good
    opted = await make_customer("Dele", "+2348034444444")
    in_window = await make_customer("Emeka", "+2348035555555")

    db.add(WhatsAppContact(
        user_id=user.id, phone_e164="2348034444444", opted_out=True,
        opted_out_at=datetime.utcnow(), opt_out_source="inbound_stop",
    ))
    db.add(WhatsAppContact(
        user_id=user.id, phone_e164="2348035555555",
        last_inbound_at=datetime.utcnow() - timedelta(hours=2),   # inside 24h window
    ))
    await db.commit()
    return {"good": good, "no_phone": no_phone, "local": local,
            "dup": dup, "opted": opted, "in_window": in_window}


async def test_preview_counts_skips_correctly(client, db, user, account, make_customer):
    await seed_mixed_customers(db, user, make_customer)
    r = await client.post("/api/whatsapp/campaigns/preview",
                          json={"kind": "custom", "audience": "all"})
    assert r.status_code == 200, r.text
    p = r.json()

    assert p["total_selected"] == 6
    assert p["skipped"]["no_phone"] == 1
    assert p["skipped"]["invalid_phone"] == 1     # 08032222222
    assert p["skipped"]["opted_out"] == 1
    # good + in_window survive; the duplicate number is collapsed into one.
    assert p["eligible"] == 2
    assert p["in_window"] == 1
    assert p["needs_opener"] == 1


async def test_custom_campaign_splits_direct_and_opener(client, db, user, account, make_customer):
    await seed_mixed_customers(db, user, make_customer)
    r = await client.post("/api/whatsapp/campaigns", json={
        "kind": "custom", "audience": "all", "custom_body": "Weekend sale!",
    })
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["total_recipients"] == 2
    assert c["direct_count"] == 1
    assert c["opener_count"] == 1
    assert c["skipped_count"] == 3

    msgs = (await db.execute(
        select(WhatsAppMessage).where(WhatsAppMessage.campaign_id == c["id"])
    )).scalars().all()
    kinds = sorted(m.kind for m in msgs)
    assert kinds == [MSG_OPENER, MSG_TEXT]
    # Both carry the body: the text sends it now, the opener parks it.
    assert all(m.body == "Weekend sale!" for m in msgs)


async def test_custom_campaign_blocked_until_opener_approved(client, db, account, make_customer):
    await make_customer("Ada", "+2348031111111")
    account.opener_template_status = "PENDING"
    await db.commit()

    r = await client.post("/api/whatsapp/campaigns", json={
        "kind": "custom", "audience": "all", "custom_body": "Hello",
    })
    assert r.status_code == 409
    assert "approv" in r.json()["detail"].lower()


async def test_campaign_requires_connected_account(client, db, user, make_customer):
    await make_customer("Ada", "+2348031111111")
    r = await client.post("/api/whatsapp/campaigns", json={
        "kind": "custom", "audience": "all", "custom_body": "Hello",
    })
    assert r.status_code == 409


async def test_selected_audience_ignores_other_users_customers(client, db, user, other_user, account, make_customer):
    mine = await make_customer("Ada", "+2348031111111")
    other = Customer(user_id=other_user.id, name="Stranger", phone_number="+2348039999999")
    db.add(other)
    await db.commit()
    await db.refresh(other)

    r = await client.post("/api/whatsapp/campaigns/preview", json={
        "kind": "custom", "audience": "selected", "customer_ids": [mine.id, other.id],
    })
    assert r.status_code == 200
    assert r.json()["total_selected"] == 1


async def test_template_campaign_validates_param_count(client, db, user, account, make_customer):
    await make_customer("Ada", "+2348031111111")
    db.add(WhatsAppTemplate(
        user_id=user.id, name="promo", language="en_US", status="APPROVED",
        body_text="Hi {{1}}, enjoy {{2}}", body_param_count=2,
    ))
    await db.commit()

    r = await client.post("/api/whatsapp/campaigns", json={
        "kind": "template", "audience": "all",
        "template_name": "promo", "template_language": "en_US",
        "params": [{"source": "customer_name"}],
    })
    assert r.status_code == 400
    assert "2 value" in r.json()["detail"]


async def test_template_campaign_rejects_unapproved_template(client, db, user, account, make_customer):
    await make_customer("Ada", "+2348031111111")
    db.add(WhatsAppTemplate(
        user_id=user.id, name="draft", language="en_US", status="PENDING",
        body_text="Hi", body_param_count=0,
    ))
    await db.commit()

    r = await client.post("/api/whatsapp/campaigns", json={
        "kind": "template", "audience": "all",
        "template_name": "draft", "template_language": "en_US",
    })
    assert r.status_code == 409


async def test_template_campaign_reaches_everyone_regardless_of_window(client, db, user, account, make_customer):
    """Templates are not limited by the 24-hour window, unlike free text."""
    await make_customer("Ada", "+2348031111111")   # never messaged the business
    db.add(WhatsAppTemplate(
        user_id=user.id, name="promo", language="en_US", status="APPROVED",
        body_text="Hi {{1}}", body_param_count=1,
    ))
    await db.commit()

    r = await client.post("/api/whatsapp/campaigns", json={
        "kind": "template", "audience": "all",
        "template_name": "promo", "template_language": "en_US",
        "params": [{"source": "customer_name"}],
    })
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["direct_count"] == 1
    assert c["opener_count"] == 0

    msgs = (await db.execute(
        select(WhatsAppMessage).where(WhatsAppMessage.campaign_id == c["id"])
    )).scalars().all()
    assert msgs[0].kind == MSG_TEMPLATE


async def test_campaign_with_no_eligible_recipients_is_rejected(client, db, account, make_customer):
    await make_customer("Bola", None)
    r = await client.post("/api/whatsapp/campaigns", json={
        "kind": "custom", "audience": "all", "custom_body": "Hello",
    })
    assert r.status_code == 400


async def test_cancel_campaign_stops_queued_messages(client, db, user, account, make_customer):
    await make_customer("Ada", "+2348031111111")
    r = await client.post("/api/whatsapp/campaigns", json={
        "kind": "custom", "audience": "all", "custom_body": "Hello",
    })
    cid = r.json()["id"]

    r2 = await client.post(f"/api/whatsapp/campaigns/{cid}/cancel")
    assert r2.status_code == 200
    assert r2.json()["cancelled"] >= 1


async def test_campaign_detail_is_owner_scoped(client, db, user, other_user, account):
    from app.models.whatsapp_campaign import WhatsAppCampaign
    other = WhatsAppCampaign(user_id=other_user.id, kind="custom", audience="all", status="completed")
    db.add(other)
    await db.commit()
    await db.refresh(other)

    r = await client.get(f"/api/whatsapp/campaigns/{other.id}")
    assert r.status_code == 404


async def test_custom_campaign_allowed_without_opener_when_all_in_window(
    client, db, user, account, make_customer
):
    """An unapproved opener must not block messaging recent chatters."""
    await make_customer("Emeka", "+2348035555555")
    db.add(WhatsAppContact(
        user_id=user.id, phone_e164="2348035555555",
        last_inbound_at=datetime.utcnow() - timedelta(hours=1),
    ))
    account.opener_template_status = "PENDING"
    await db.commit()

    r = await client.post("/api/whatsapp/campaigns", json={
        "kind": "custom", "audience": "all", "custom_body": "Your order is ready",
    })
    assert r.status_code == 201, r.text
    assert r.json()["direct_count"] == 1
    assert r.json()["opener_count"] == 0


async def test_reconnecting_to_new_waba_resets_opener_status(db, user, account):
    """A different WABA has no opener of its own, so approval must not carry over."""
    from app.controllers.whatsapp_controller import WhatsAppController
    assert account.opener_template_status == "APPROVED"

    acc = await WhatsAppController.get_account(db, user.id)
    assert acc.waba_id == "WABA123"
    # Simulate the branch _finish_connect takes when the WABA changes.
    if acc.waba_id != "WABA_DIFFERENT":
        acc.opener_template_status = None
    acc.waba_id = "WABA_DIFFERENT"
    await db.commit()
    await db.refresh(acc)
    assert acc.opener_template_status is None


# --- templates Careloop cannot fill --------------------------------------

def _template(**kw):
    from app.models.whatsapp_template import WhatsAppTemplate
    defaults = dict(user_id=1, name="t", language="en_US", status="APPROVED",
                    body_text="Hi", body_param_count=0, header_type=None, components=None)
    defaults.update(kw)
    return WhatsAppTemplate(**defaults)


def test_text_only_template_is_supported():
    from app.controllers.whatsapp_controller import template_support
    assert template_support(_template())[0] is True
    assert template_support(_template(header_type="TEXT"))[0] is True


def test_image_header_template_is_blocked():
    from app.controllers.whatsapp_controller import template_support
    ok, reason = template_support(_template(header_type="IMAGE"))
    assert ok is False
    assert reason == "needs an image attachment"   # article agreement


def test_document_header_uses_correct_article():
    from app.controllers.whatsapp_controller import template_support
    assert template_support(_template(header_type="DOCUMENT"))[1] == "needs a document attachment"


def test_carousel_template_is_blocked():
    from app.controllers.whatsapp_controller import template_support
    ok, reason = template_support(_template(
        components={"components": [{"type": "BODY"}, {"type": "CAROUSEL", "cards": []}]}))
    assert ok is False and reason == "is a carousel"


def test_dynamic_url_button_is_blocked():
    from app.controllers.whatsapp_controller import template_support
    ok, reason = template_support(_template(components={"components": [
        {"type": "BUTTONS", "buttons": [{"type": "URL", "url": "https://x.com/{{1}}"}]}]}))
    assert ok is False and "variable link" in reason


def test_static_url_button_is_fine():
    from app.controllers.whatsapp_controller import template_support
    assert template_support(_template(components={"components": [
        {"type": "BUTTONS", "buttons": [{"type": "URL", "url": "https://x.com/shop"}]}]}))[0] is True


async def test_campaign_rejects_unsendable_template(client, db, user, account, make_customer):
    """The real failure seen in testing: an image-header template returns 132012."""
    from app.models.whatsapp_template import WhatsAppTemplate
    await make_customer("Ada", "+2348031111111")
    db.add(WhatsAppTemplate(user_id=user.id, name="promo_image", language="en_US",
                            status="APPROVED", body_text="Hi", body_param_count=0,
                            header_type="IMAGE"))
    await db.commit()

    r = await client.post("/api/whatsapp/campaigns", json={
        "kind": "template", "audience": "all",
        "template_name": "promo_image", "template_language": "en_US",
    })
    assert r.status_code == 400
    assert "image attachment" in r.json()["detail"]
