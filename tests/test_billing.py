"""Subscription plans: limits, plan changes, webhooks and grace periods.

Paystack is replaced by a fake, so these run offline:
    pytest tests/test_billing.py
"""
import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.customer import Customer
from app.models.subscription import Subscription
from app.models.user import User
from app.plans import PLANS
from app.services import billing_service
from app.services.customer_import_service import import_customer_rows

CALLBACK = "https://example.test/dashboard?page=billing&checkout=return"


class FakePaystack:
    configured = True

    def __init__(self):
        self.calls = []
        self._next = 0

    def _code(self, prefix):
        self._next += 1
        return f"{prefix}_{self._next}"

    async def initialize_transaction(self, **kwargs):
        self.calls.append(("initialize", kwargs))
        return {"authorization_url": "https://checkout.paystack.test/x", "reference": self._code("ref")}

    async def create_subscription(self, **kwargs):
        self.calls.append(("create_subscription", kwargs))
        return {"subscription_code": self._code("SUB_sched"), "email_token": self._code("tok")}

    async def disable_subscription(self, code, token=None):
        self.calls.append(("disable", code))

    async def enable_subscription(self, code, token=None):
        self.calls.append(("enable", code))

    async def manage_link(self, code):
        return f"https://paystack.test/manage/{code}"

    def disabled(self):
        return [c[1] for c in self.calls if c[0] == "disable"]


@pytest.fixture
def env(monkeypatch):
    for plan in ("basic", "standard", "premium", "automation"):
        monkeypatch.setenv(f"PAYSTACK_PLAN_{plan.upper()}", f"PLN_{plan}")
    monkeypatch.setitem(PLANS["automation"], "price", 15000)
    paystack = FakePaystack()
    monkeypatch.setattr(billing_service, "paystack_service", paystack)
    emails = []
    for name in ("send_payment_failed", "send_plan_ended", "send_card_expiring"):
        monkeypatch.setattr(billing_service.email_service, name,
                            lambda *a, _n=name, **k: emails.append((_n, a)) or True)
    return paystack, emails


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


async def make_user(db, customers=0, email="owner@example.com"):
    user = User(email=email, full_name="Ada Owner", is_email_verified=True)
    db.add(user)
    await db.flush()
    for i in range(customers):
        db.add(Customer(user_id=user.id, name=f"Customer {i}", phone_number=f"+2348000000{i:03d}"))
    await db.commit()
    return user


async def subscribe(db, user, plan, sub_code, period_end=None):
    """Put the user on a paid plan as if checkout and its webhooks had completed."""
    sub = await billing_service.get_or_create_subscription(db, user.id)
    sub.plan = plan
    sub.paystack_customer_code = "CUS_1"
    sub.paystack_authorization_code = "AUTH_1"
    sub.paystack_subscription_code = sub_code
    sub.paystack_email_token = "tok_current"
    sub.current_period_end = period_end or datetime.utcnow() + timedelta(days=20)
    await db.commit()
    return sub


def charge_success(user, plan, meta=None):
    return {"event": "charge.success", "data": {
        "plan": {"plan_code": f"PLN_{plan}"},
        "paid_at": datetime.utcnow().isoformat() + "Z",
        "metadata": meta or {},
        "customer": {"customer_code": "CUS_1", "email": user.email},
        "authorization": {"authorization_code": "AUTH_1", "reusable": True},
    }}


def subscription_create(user, plan, code):
    return {"event": "subscription.create", "data": {
        "subscription_code": code, "email_token": f"tok_{code}",
        "plan": {"plan_code": f"PLN_{plan}"},
        "next_payment_date": (datetime.utcnow() + timedelta(days=30)).isoformat() + "Z",
        "customer": {"customer_code": "CUS_1", "email": user.email},
    }}


async def checkout_metadata(paystack):
    return [c for c in paystack.calls if c[0] == "initialize"][-1][1]["metadata"]


# ─── Limits ───

def test_free_user_can_add_10_customers_and_no_more(db, env):
    async def go():
        user = await make_user(db, customers=9)
        await billing_service.ensure_can_add_customer(db, user.id)  # 10th is fine
        db.add(Customer(user_id=user.id, name="Tenth"))
        await db.commit()
        with pytest.raises(HTTPException) as e:
            await billing_service.ensure_can_add_customer(db, user.id)
        assert e.value.status_code == 403
        assert e.value.detail["code"] == "customer_limit_reached"
    run(go())


def test_basic_user_with_150_customers_cannot_add_151st(db, env):
    async def go():
        user = await make_user(db, customers=150)
        await subscribe(db, user, "basic", "SUB_basic")
        with pytest.raises(HTTPException) as e:
            await billing_service.ensure_can_add_customer(db, user.id)
        assert e.value.detail["limit"] == 150
    run(go())


def test_import_stops_at_the_limit_and_reports_skipped_rows(db, env):
    async def go():
        user = await make_user(db, customers=8)
        rows = [{"name": f"New {i}", "phone_number": f"+2348111111{i:03d}"} for i in range(5)]
        result = await import_customer_rows(
            db, user.id, rows, max_new=await billing_service.remaining_customer_slots(db, user.id)
        )
        assert result["created"] == 2
        assert len(result["failed"]) == 3
        assert result["limit_reached"] is True
        assert await billing_service.count_customers(db, user.id) == 10
    run(go())


# ─── Buying a plan ───

@pytest.mark.parametrize("order", ["charge_first", "subscription_first"])
def test_payment_webhooks_switch_the_plan(db, env, order):
    paystack, _ = env

    async def go():
        user = await make_user(db, customers=10)
        result = await billing_service.change_plan(db, user, "basic", CALLBACK)
        assert result["action"] == "redirect"
        assert await billing_service.current_plan(db, user.id) == "free"  # not from the redirect

        meta = await checkout_metadata(paystack)
        events = [charge_success(user, "basic", meta), subscription_create(user, "basic", "SUB_new")]
        if order == "subscription_first":
            events.reverse()
        for event in events:
            await billing_service.handle_webhook(db, event)

        sub = await billing_service.get_subscription(db, user.id)
        assert sub.plan == "basic"
        assert sub.paystack_subscription_code == "SUB_new"
        assert await billing_service.remaining_customer_slots(db, user.id) == 140
    run(go())


def test_free_user_can_buy_automation_directly(db, env):
    paystack, _ = env

    async def go():
        user = await make_user(db, customers=10)
        with pytest.raises(HTTPException):
            await billing_service.change_plan(db, user, "automation", CALLBACK)  # Meta notice not ticked
        await billing_service.change_plan(db, user, "automation", CALLBACK, meta_fees_acknowledged=True)
        await billing_service.handle_webhook(db, charge_success(user, "automation", await checkout_metadata(paystack)))
        status = await billing_service.billing_status(db, user.id)
        assert status["plan"] == "automation"
        assert status["customer_limit"] is None
        assert status["bulk_messaging"] is True
    run(go())


def test_standard_user_buying_automation_has_standard_disabled(db, env):
    paystack, _ = env

    async def go():
        user = await make_user(db)
        await subscribe(db, user, "standard", "SUB_standard")
        await billing_service.change_plan(db, user, "automation", CALLBACK, meta_fees_acknowledged=True)
        meta = await checkout_metadata(paystack)
        assert meta["replaces"] == ["SUB_standard"]
        assert await billing_service.current_plan(db, user.id) == "standard"

        await billing_service.handle_webhook(db, charge_success(user, "automation", meta))
        await billing_service.handle_webhook(db, subscription_create(user, "automation", "SUB_auto"))
        # Paystack then confirms the old subscription is off; that must not touch the new plan.
        await billing_service.handle_webhook(db, {"event": "subscription.disable", "data": {
            "subscription_code": "SUB_standard", "customer": {"customer_code": "CUS_1"}}})

        assert "SUB_standard" in paystack.disabled()
        sub = await billing_service.get_subscription(db, user.id)
        assert (sub.plan, sub.paystack_subscription_code, sub.pending_plan) == ("automation", "SUB_auto", None)
    run(go())


# ─── Downgrades and cancelling ───

def test_downgrade_waits_for_the_end_of_the_paid_month(db, env):
    paystack, _ = env

    async def go():
        user = await make_user(db, customers=200)
        period_end = datetime.utcnow() + timedelta(days=12)
        await subscribe(db, user, "premium", "SUB_premium", period_end)

        assert (await billing_service.change_plan(db, user, "basic", CALLBACK))["action"] == "updated"
        created = [c[1] for c in paystack.calls if c[0] == "create_subscription"][0]
        assert created["plan_code"] == "PLN_basic"
        assert created["start_date"] == period_end
        assert "SUB_premium" in paystack.disabled()

        # Webhooks caused by the change don't end Premium early.
        await billing_service.handle_webhook(db, {"event": "subscription.not_renew", "data": {
            "subscription_code": "SUB_premium", "customer": {"customer_code": "CUS_1"}}})
        sub = await billing_service.get_subscription(db, user.id)
        await billing_service.handle_webhook(db, subscription_create(user, "basic", sub.pending_subscription_code))
        sub = await billing_service.get_subscription(db, user.id)
        assert (sub.plan, sub.pending_plan) == ("premium", "basic")
        assert await billing_service.remaining_customer_slots(db, user.id) is None

        # At the end of the month Paystack charges the Basic subscription.
        await billing_service.handle_webhook(db, charge_success(user, "basic"))
        sub = await billing_service.get_subscription(db, user.id)
        assert (sub.plan, sub.pending_plan) == ("basic", None)
        # Over the new limit: nothing is deleted, but nothing new can be added.
        assert await billing_service.count_customers(db, user.id) == 200
        with pytest.raises(HTTPException):
            await billing_service.ensure_can_add_customer(db, user.id)
    run(go())


def test_cancelled_plan_lasts_until_period_end_then_drops_to_free(db, env):
    paystack, _ = env

    async def go():
        user = await make_user(db, customers=40)
        sub = await subscribe(db, user, "standard", "SUB_standard")
        await billing_service.change_plan(db, user, "free", CALLBACK)
        await billing_service.process_subscriptions(db)
        assert (await billing_service.get_subscription(db, user.id)).plan == "standard"

        sub.current_period_end = datetime.utcnow() - timedelta(minutes=1)
        await db.commit()
        await billing_service.process_subscriptions(db)
        assert (await billing_service.get_subscription(db, user.id)).plan == "free"
        assert await billing_service.count_customers(db, user.id) == 40
    run(go())


def test_keep_current_plan_undoes_a_scheduled_downgrade(db, env):
    paystack, _ = env

    async def go():
        user = await make_user(db)
        await subscribe(db, user, "premium", "SUB_premium")
        await billing_service.change_plan(db, user, "basic", CALLBACK)
        scheduled = (await billing_service.get_subscription(db, user.id)).pending_subscription_code
        await billing_service.change_plan(db, user, "premium", CALLBACK)
        sub = await billing_service.get_subscription(db, user.id)
        assert sub.pending_plan is None
        assert ("enable", "SUB_premium") in paystack.calls
        assert scheduled in paystack.disabled()
    run(go())


# ─── Failed payments ───

def test_failed_payment_gets_reminders_then_free_limits_after_grace(db, env):
    _, emails = env

    async def go():
        user = await make_user(db, customers=60)
        await subscribe(db, user, "basic", "SUB_basic")
        await billing_service.handle_webhook(db, {"event": "invoice.payment_failed", "data": {
            "subscription": {"subscription_code": "SUB_basic"}, "customer": {"customer_code": "CUS_1"}}})
        sub = await billing_service.get_subscription(db, user.id)
        assert sub.status == "past_due"

        await billing_service.process_subscriptions(db)
        await billing_service.process_subscriptions(db)  # only one reminder a day
        assert [e[0] for e in emails] == ["send_payment_failed"]
        assert sub.plan == "basic"

        sub.payment_failed_at = datetime.utcnow() - timedelta(days=3, minutes=1)
        await db.commit()
        await billing_service.process_subscriptions(db)
        sub = await billing_service.get_subscription(db, user.id)
        assert (sub.plan, sub.status) == ("free", "active")
        assert emails[-1][0] == "send_plan_ended"
        assert await billing_service.count_customers(db, user.id) == 60
    run(go())


def test_paying_during_grace_restores_the_plan(db, env):
    paystack, _ = env

    async def go():
        user = await make_user(db)
        sub = await subscribe(db, user, "basic", "SUB_basic")
        sub.status, sub.payment_failed_at = "past_due", datetime.utcnow()
        await db.commit()
        assert (await billing_service.change_plan(db, user, "basic", CALLBACK))["action"] == "redirect"
        assert "SUB_basic" in paystack.disabled()
        await billing_service.handle_webhook(db, charge_success(user, "basic", await checkout_metadata(paystack)))
        sub = await billing_service.get_subscription(db, user.id)
        assert (sub.plan, sub.status, sub.payment_failed_at) == ("basic", "active", None)
    run(go())


def test_expiring_card_sends_update_link(db, env):
    _, emails = env

    async def go():
        user = await make_user(db)
        await subscribe(db, user, "basic", "SUB_basic")
        await billing_service.handle_webhook(db, {"event": "subscription.expiring_cards", "data": [
            {"subscription": {"subscription_code": "SUB_basic"}, "customer": {"customer_code": "CUS_1"}}]})
        assert emails == [("send_card_expiring", (user.email, "Ada Owner", "Basic", "https://paystack.test/manage/SUB_basic"))]
    run(go())


# ─── Webhook endpoint ───

def test_webhook_rejects_bad_signatures(db, env, monkeypatch):
    import httpx
    from app.main import app
    from app.database import get_db
    from app.routes import billing as billing_routes

    monkeypatch.setattr(billing_routes.paystack_service, "secret_key", "sk_test_x")

    async def go():
        user = await make_user(db)

        async def override_db():
            yield db
        app.dependency_overrides[get_db] = override_db
        try:
            body = json.dumps(charge_success(user, "basic", {"careloop_checkout": True, "user_id": user.id})).encode()
            good = hmac.new(b"sk_test_x", body, hashlib.sha512).hexdigest()
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                bad = await client.post("/api/billing/paystack/webhook", content=body,
                                        headers={"x-paystack-signature": "nope"})
                assert bad.status_code == 401
                assert await billing_service.current_plan(db, user.id) == "free"
                ok = await client.post("/api/billing/paystack/webhook", content=body,
                                       headers={"x-paystack-signature": good})
                assert ok.status_code == 200
            assert await billing_service.current_plan(db, user.id) == "basic"
        finally:
            app.dependency_overrides.clear()
    run(go())
