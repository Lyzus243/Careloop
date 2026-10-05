"""Careloop plans: customer limits, plan changes, Paystack webhooks and grace periods.

A user's plan only changes in response to Paystack webhooks (or when a paid
period runs out), never from the browser redirect after checkout.

- Upgrades (and any purchase from Free) go through Paystack checkout and apply
  as soon as the payment webhook arrives. The old subscription is disabled with
  no refund.
- Downgrades and cancellations wait for the end of the paid month. A downgrade
  creates the cheaper Paystack subscription now, starting on that date, and
  stops the current one renewing.
- Paystack doesn't retry failed renewals, so a failure starts a grace period of
  PAYMENT_GRACE_DAYS, after which the user drops to Free limits. Their data is
  never deleted.
"""
import calendar
import json
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select, delete, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.models.subscription import Subscription
from app.models.user import User
from app.plans import (
    PLANS, FREE_PLAN, PAYMENT_GRACE_DAYS,
    plan_rank, paystack_plan_code, plan_for_paystack_code, is_purchasable,
)
from app.services.paystack_service import paystack_service, PaystackError
from app.services.email_service import email_service

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.utcnow()


def parse_paystack_date(value) -> Optional[datetime]:
    """Paystack sends ISO 8601 strings; store them as naive UTC like the rest of the app."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def add_month(dt: datetime) -> datetime:
    year, month = (dt.year + 1, 1) if dt.month == 12 else (dt.year, dt.month + 1)
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


# ─── Plans and limits ───

async def get_subscription(db: AsyncSession, user_id: int) -> Optional[Subscription]:
    result = await db.execute(select(Subscription).where(Subscription.user_id == user_id))
    return result.scalar_one_or_none()


async def get_or_create_subscription(db: AsyncSession, user_id: int) -> Subscription:
    sub = await get_subscription(db, user_id)
    if sub is None:
        sub = Subscription(user_id=user_id, plan=FREE_PLAN, status="active")
        db.add(sub)
        # Committed straight away: callers go on to call Paystack, and an open write
        # transaction during that call would lock SQLite for every other request.
        await db.commit()
    return sub


async def count_customers(db: AsyncSession, user_id: int) -> int:
    result = await db.execute(select(func.count()).select_from(Customer).where(Customer.user_id == user_id))
    return result.scalar_one()


async def current_plan(db: AsyncSession, user_id: int) -> str:
    sub = await get_subscription(db, user_id)
    return sub.plan if sub else FREE_PLAN


async def remaining_customer_slots(db: AsyncSession, user_id: int) -> Optional[int]:
    """How many more customers the user can add, or None when unlimited."""
    limit = PLANS[await current_plan(db, user_id)]["customer_limit"]
    if limit is None:
        return None
    return max(0, limit - await count_customers(db, user_id))


def limit_reached_detail(plan: str) -> dict:
    limit = PLANS[plan]["customer_limit"]
    return {
        "code": "customer_limit_reached",
        "message": f"You've reached the {limit}-customer limit on the {PLANS[plan]['name']} plan. "
                   "Upgrade your plan to add more customers.",
        "plan": plan,
        "limit": limit,
    }


async def ensure_can_add_customer(db: AsyncSession, user_id: int) -> None:
    if await remaining_customer_slots(db, user_id) == 0:
        plan = await current_plan(db, user_id)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=limit_reached_detail(plan))


async def billing_status(db: AsyncSession, user_id: int) -> dict:
    sub = await get_subscription(db, user_id)
    plan = sub.plan if sub else FREE_PLAN
    grace_ends_at = None
    if sub and sub.status == "past_due" and sub.payment_failed_at:
        grace_ends_at = sub.payment_failed_at + timedelta(days=PAYMENT_GRACE_DAYS)
    return {
        "plan": plan,
        "plan_name": PLANS[plan]["name"],
        "status": sub.status if sub else "active",
        "customer_count": await count_customers(db, user_id),
        "customer_limit": PLANS[plan]["customer_limit"],
        "bulk_messaging": PLANS[plan]["bulk_messaging"],
        "current_period_end": sub.current_period_end.isoformat() + "Z" if sub and sub.current_period_end and plan != FREE_PLAN else None,
        "pending_plan": sub.pending_plan if sub else None,
        "grace_ends_at": grace_ends_at.isoformat() + "Z" if grace_ends_at else None,
        "can_update_card": bool(sub and sub.paystack_subscription_code),
        "payments_enabled": paystack_service.configured,
        "plans": public_plans(),
    }


def public_plans() -> list[dict]:
    """The plan list shown on the landing page and the billing page."""
    return [
        {
            "key": key,
            "name": p["name"],
            "customer_limit": p["customer_limit"],
            "price": p["price"],
            "original_price": p["original_price"],
            "bulk_messaging": p["bulk_messaging"],
            "available": key == FREE_PLAN or is_purchasable(key),
        }
        for key, p in PLANS.items()
    ]


# ─── Plan changes requested from the dashboard ───

async def _disable_quietly(code: Optional[str], token: Optional[str] = None) -> None:
    """Stop a Paystack subscription renewing. Failures are logged, not raised,
    because the subscription may already be disabled."""
    if not code:
        return
    try:
        await paystack_service.disable_subscription(code, token)
    except PaystackError as e:
        logger.warning(f"PAYSTACK: could not disable {code}: {e}")


async def _start_checkout(sub: Subscription, user: User, plan: str, callback_url: str, cancel_url: Optional[str]) -> dict:
    replaces = [c for c in (sub.paystack_subscription_code, sub.pending_subscription_code) if c]
    try:
        data = await paystack_service.initialize_transaction(
            email=user.email,
            plan_code=paystack_plan_code(plan),
            amount_kobo=PLANS[plan]["price"] * 100,
            callback_url=callback_url,
            metadata={
                "careloop_checkout": True, "user_id": user.id, "plan": plan, "replaces": replaces,
                "cancel_action": cancel_url or callback_url,
            },
        )
    except PaystackError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not start checkout: {e}")
    return {"action": "redirect", "authorization_url": data["authorization_url"], "reference": data["reference"]}


async def change_plan(
    db: AsyncSession, user: User, target: str, callback_url: str,
    meta_fees_acknowledged: bool = False, cancel_url: Optional[str] = None,
) -> dict:
    if target not in PLANS:
        raise HTTPException(status_code=400, detail="Unknown plan.")
    if target != FREE_PLAN and not paystack_service.configured:
        raise HTTPException(status_code=503, detail="Payments are not set up yet.")
    if target == "automation" and not meta_fees_acknowledged:
        raise HTTPException(status_code=400, detail="Please confirm you understand Meta's charges are separate.")

    sub = await get_or_create_subscription(db, user.id)

    if target == sub.plan:
        if sub.pending_plan:
            return await _keep_current_plan(db, sub)
        if sub.status == "past_due":
            # Pay now for the plan whose renewal failed. Paystack won't retry the failed
            # charge, so stop that subscription and start a fresh one through checkout.
            # The grace period keeps running until the payment webhook arrives.
            await _disable_quietly(sub.paystack_subscription_code, sub.paystack_email_token)
            sub.paystack_subscription_code = None
            sub.paystack_email_token = None
            await db.commit()
            return await _start_checkout(sub, user, target, callback_url, cancel_url)
        raise HTTPException(status_code=400, detail=f"You're already on the {PLANS[target]['name']} plan.")

    if target == FREE_PLAN:
        return await _cancel(db, sub)

    if not is_purchasable(target):
        raise HTTPException(status_code=400, detail=f"The {PLANS[target]['name']} plan isn't available yet.")

    if sub.plan == FREE_PLAN or sub.status == "past_due" or plan_rank(target) > plan_rank(sub.plan):
        return await _start_checkout(sub, user, target, callback_url, cancel_url)

    return await _schedule_downgrade(db, sub, target)


async def _cancel(db: AsyncSession, sub: Subscription) -> dict:
    if sub.status == "past_due":
        # Nothing has been paid for the current period, so there's nothing left to wait for.
        await _end_paid_plan(db, sub)
        await db.commit()
        return {"action": "updated"}
    if sub.pending_plan is None and sub.paystack_subscription_code:
        try:
            await paystack_service.disable_subscription(sub.paystack_subscription_code, sub.paystack_email_token)
        except PaystackError as e:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not cancel your plan: {e}")
    old_pending = _clear_pending(sub)
    sub.pending_plan = FREE_PLAN
    await db.commit()
    await _disable_quietly(*old_pending)
    return {"action": "updated"}


def _clear_pending(sub: Subscription) -> tuple:
    """Forget the scheduled subscription and return its (code, token) so the caller can
    disable it after committing. Committing first means the disable webhook no longer
    matches it and can't be mistaken for the user cancelling."""
    old = (sub.pending_subscription_code, sub.pending_email_token)
    sub.pending_plan = None
    sub.pending_subscription_code = None
    sub.pending_email_token = None
    return old


async def _schedule_downgrade(db: AsyncSession, sub: Subscription, target: str) -> dict:
    if not (sub.paystack_customer_code and sub.paystack_authorization_code and sub.current_period_end):
        raise HTTPException(
            status_code=409,
            detail="We couldn't schedule this change. Cancel your current plan, then choose the new one when it ends.",
        )
    current_renews = sub.pending_plan is None
    previous_pending = sub.pending_plan
    old_pending = _clear_pending(sub)

    # Saved before calling Paystack so the subscription.create webhook, which can
    # arrive before Paystack's response, is recognised as this scheduled change.
    sub.pending_plan = target
    await db.commit()

    try:
        created = await paystack_service.create_subscription(
            customer_code=sub.paystack_customer_code,
            plan_code=paystack_plan_code(target),
            authorization_code=sub.paystack_authorization_code,
            start_date=sub.current_period_end,
        )
    except PaystackError as e:
        sub.pending_plan, sub.pending_subscription_code, sub.pending_email_token = previous_pending, *old_pending
        await db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not schedule the change: {e}")

    await db.refresh(sub)
    sub.pending_subscription_code = created.get("subscription_code")
    sub.pending_email_token = created.get("email_token") or sub.pending_email_token
    await db.commit()

    if current_renews:
        try:
            await paystack_service.disable_subscription(sub.paystack_subscription_code, sub.paystack_email_token)
        except PaystackError as e:
            # Don't leave the user paying for both plans next month.
            new_pending = _clear_pending(sub)
            await db.commit()
            await _disable_quietly(*new_pending)
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not schedule the change: {e}")
    await _disable_quietly(*old_pending)
    return {"action": "updated"}


async def _keep_current_plan(db: AsyncSession, sub: Subscription) -> dict:
    """Undo a scheduled downgrade or cancellation."""
    if sub.status == "past_due" or not sub.paystack_subscription_code:
        raise HTTPException(status_code=409, detail="This plan can't be resumed. Please choose a plan again.")
    try:
        await paystack_service.enable_subscription(sub.paystack_subscription_code, sub.paystack_email_token)
    except PaystackError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not keep your plan: {e}")
    old_pending = _clear_pending(sub)
    await db.commit()
    await _disable_quietly(*old_pending)
    return {"action": "updated"}


async def card_update_link(db: AsyncSession, user_id: int) -> str:
    sub = await get_subscription(db, user_id)
    if not sub or not sub.paystack_subscription_code:
        raise HTTPException(status_code=404, detail="You don't have a card on file.")
    try:
        return await paystack_service.manage_link(sub.paystack_subscription_code)
    except PaystackError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not get the card update link: {e}")


async def cancel_for_deleted_account(db: AsyncSession, user_id: int) -> None:
    sub = await get_subscription(db, user_id)
    if not sub:
        return
    await _disable_quietly(sub.paystack_subscription_code, sub.paystack_email_token)
    await _disable_quietly(sub.pending_subscription_code, sub.pending_email_token)
    await db.execute(delete(Subscription).where(Subscription.user_id == user_id))


# ─── State changes ───

async def _end_paid_plan(db: AsyncSession, sub: Subscription) -> None:
    """Drop to Free limits. Customers are kept; the user just can't add more while over 10."""
    to_disable = [(sub.paystack_subscription_code, sub.paystack_email_token), _clear_pending(sub)]
    sub.plan = FREE_PLAN
    sub.status = "active"
    sub.current_period_end = None
    sub.payment_failed_at = None
    sub.last_payment_reminder_at = None
    sub.paystack_subscription_code = None
    sub.paystack_email_token = None
    await db.commit()
    for code, token in to_disable:
        await _disable_quietly(code, token)


def _promote_pending(sub: Subscription) -> None:
    """The scheduled cheaper subscription has started, so it becomes the current one."""
    sub.plan = sub.pending_plan
    sub.paystack_subscription_code = sub.pending_subscription_code
    sub.paystack_email_token = sub.pending_email_token
    sub.pending_plan = None
    sub.pending_subscription_code = None
    sub.pending_email_token = None


def _mark_paid(sub: Subscription, period_end: Optional[datetime]) -> None:
    sub.status = "active"
    sub.payment_failed_at = None
    sub.last_payment_reminder_at = None
    if period_end:
        sub.current_period_end = period_end


# ─── Webhooks ───

def _plan_code_of(value) -> Optional[str]:
    if isinstance(value, dict):
        return value.get("plan_code")
    return value if isinstance(value, str) else None


def _metadata_of(data: dict) -> dict:
    meta = data.get("metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except ValueError:
            meta = None
    return meta if isinstance(meta, dict) else {}


async def _find_subscription(
    db: AsyncSession,
    user_id=None,
    subscription_code: Optional[str] = None,
    customer: Optional[dict] = None,
) -> Optional[Subscription]:
    """Match a webhook to a user, most specific identifier first."""
    if user_id:
        try:
            user = await db.get(User, int(user_id))
        except (TypeError, ValueError):
            user = None
        if user:
            return await get_or_create_subscription(db, user.id)

    if subscription_code:
        result = await db.execute(select(Subscription).where(or_(
            Subscription.paystack_subscription_code == subscription_code,
            Subscription.pending_subscription_code == subscription_code,
        )))
        sub = result.scalars().first()
        if sub:
            return sub

    customer = customer or {}
    if customer.get("customer_code"):
        result = await db.execute(
            select(Subscription).where(Subscription.paystack_customer_code == customer["customer_code"])
        )
        sub = result.scalars().first()
        if sub:
            return sub

    if customer.get("email"):
        result = await db.execute(select(User).where(func.lower(User.email) == customer["email"].strip().lower()))
        user = result.scalars().first()
        if user:
            return await get_or_create_subscription(db, user.id)
    return None


async def handle_webhook(db: AsyncSession, event: dict) -> None:
    handlers = {
        "charge.success": _on_charge_success,
        "subscription.create": _on_subscription_create,
        "invoice.create": _on_invoice,
        "invoice.update": _on_invoice,
        "invoice.payment_failed": _on_invoice_payment_failed,
        "subscription.not_renew": _on_subscription_ended,
        "subscription.disable": _on_subscription_ended,
        "subscription.expiring_cards": _on_expiring_cards,
    }
    handler = handlers.get(event.get("event"))
    if handler:
        await handler(db, event.get("data") or {}, event.get("event"))
        await db.commit()


async def _on_charge_success(db: AsyncSession, data: dict, _event: str) -> None:
    plan = plan_for_paystack_code(_plan_code_of(data.get("plan")))
    if not plan:
        return  # not a subscription charge for one of our plans
    meta = _metadata_of(data)
    sub = await _find_subscription(db, user_id=meta.get("user_id"), customer=data.get("customer"))
    if not sub:
        logger.warning(f"PAYSTACK: charge.success for unknown customer {data.get('customer')}")
        return

    customer_code = (data.get("customer") or {}).get("customer_code")
    if customer_code:
        sub.paystack_customer_code = customer_code
    authorization = data.get("authorization") or {}
    if authorization.get("reusable") and authorization.get("authorization_code"):
        sub.paystack_authorization_code = authorization["authorization_code"]

    paid_at = parse_paystack_date(data.get("paid_at") or data.get("paidAt")) or utcnow()

    if meta.get("careloop_checkout"):
        # A plan bought in the dashboard replaces whatever the user had, with no refund.
        replaced = set(meta.get("replaces") or [])
        if sub.paystack_subscription_code in replaced:
            sub.paystack_subscription_code = None
            sub.paystack_email_token = None
        _clear_pending(sub)
        sub.plan = plan
        _mark_paid(sub, add_month(paid_at))
        await db.commit()
        for code in replaced:
            await _disable_quietly(code)
        return

    if sub.pending_plan == plan and sub.pending_subscription_code:
        _promote_pending(sub)
    else:
        sub.plan = plan
    _mark_paid(sub, add_month(paid_at))


async def _on_subscription_create(db: AsyncSession, data: dict, _event: str) -> None:
    code = data.get("subscription_code")
    plan = plan_for_paystack_code(_plan_code_of(data.get("plan")))
    sub = await _find_subscription(db, subscription_code=code, customer=data.get("customer"))
    if not sub or not code or not plan:
        return

    is_scheduled = code == sub.pending_subscription_code or (
        sub.pending_plan == plan and sub.pending_subscription_code is None
    )
    if is_scheduled:
        sub.pending_subscription_code = code
        sub.pending_email_token = data.get("email_token")
        return
    if code == sub.paystack_subscription_code:
        sub.paystack_email_token = data.get("email_token") or sub.paystack_email_token
        return

    # Paystack creates a subscription after a successful checkout payment.
    sub.paystack_subscription_code = code
    sub.paystack_email_token = data.get("email_token")
    customer_code = (data.get("customer") or {}).get("customer_code")
    if customer_code:
        sub.paystack_customer_code = customer_code
    sub.plan = plan
    _mark_paid(sub, parse_paystack_date(data.get("next_payment_date")) or add_month(utcnow()))


async def _on_invoice(db: AsyncSession, data: dict, _event: str) -> None:
    if not data.get("paid"):
        return  # invoice.create arrives before the charge; failures come as invoice.payment_failed
    subscription = data.get("subscription") or {}
    code = subscription.get("subscription_code")
    sub = await _find_subscription(db, subscription_code=code, customer=data.get("customer"))
    if not sub or not code:
        return
    if code == sub.pending_subscription_code:
        _promote_pending(sub)
    elif code != sub.paystack_subscription_code:
        return
    _mark_paid(sub, parse_paystack_date(subscription.get("next_payment_date")))


async def _on_invoice_payment_failed(db: AsyncSession, data: dict, _event: str) -> None:
    code = (data.get("subscription") or {}).get("subscription_code")
    sub = await _find_subscription(db, subscription_code=code, customer=data.get("customer"))
    if not sub or not code:
        return
    if code == sub.pending_subscription_code:
        _promote_pending(sub)
    elif code != sub.paystack_subscription_code:
        return
    if sub.status != "past_due":
        sub.status = "past_due"
        sub.payment_failed_at = utcnow()
        sub.last_payment_reminder_at = None  # the background job sends the first reminder


async def _on_subscription_ended(db: AsyncSession, data: dict, event: str) -> None:
    """The subscription won't renew: cancelled by Careloop, or by the user from a Paystack email."""
    code = data.get("subscription_code")
    sub = await _find_subscription(db, subscription_code=code, customer=data.get("customer"))
    if not sub or not code:
        return
    if code == sub.pending_subscription_code:
        sub.pending_plan = FREE_PLAN
        sub.pending_subscription_code = None
        sub.pending_email_token = None
        return
    if code != sub.paystack_subscription_code or sub.plan == FREE_PLAN:
        return  # a subscription Careloop already replaced
    if sub.pending_plan is None:
        sub.pending_plan = FREE_PLAN
    period_over = sub.current_period_end is None or utcnow() >= sub.current_period_end
    if event == "subscription.disable" and period_over and sub.status != "past_due" and sub.pending_plan == FREE_PLAN:
        await _end_paid_plan(db, sub)


async def _on_expiring_cards(db: AsyncSession, data, _event: str) -> None:
    for item in data if isinstance(data, list) else []:
        code = (item.get("subscription") or {}).get("subscription_code")
        sub = await _find_subscription(db, subscription_code=code, customer=item.get("customer"))
        if not sub or code != sub.paystack_subscription_code:
            continue
        user = await db.get(User, sub.user_id)
        try:
            link = await paystack_service.manage_link(code)
        except PaystackError as e:
            logger.warning(f"PAYSTACK: no card update link for {code}: {e}")
            continue
        email_service.send_card_expiring(user.email, user.full_name or "there", PLANS[sub.plan]["name"], link)


# ─── Background job ───

async def process_subscriptions(db: AsyncSession) -> None:
    """End lapsed plans and send failed-payment reminders. Runs every hour."""
    now = utcnow()
    grace = timedelta(days=PAYMENT_GRACE_DAYS)
    result = await db.execute(select(Subscription).where(or_(
        Subscription.plan != FREE_PLAN, Subscription.pending_plan.isnot(None)
    )))
    for sub in result.scalars().all():
        user = await db.get(User, sub.user_id)
        name = (user.full_name if user else None) or "there"
        plan_name = PLANS[sub.plan]["name"]
        period_end = sub.current_period_end

        if sub.status == "past_due" and sub.payment_failed_at and now >= sub.payment_failed_at + grace:
            await _end_paid_plan(db, sub)
            if user:
                email_service.send_plan_ended(user.email, name, plan_name)
        elif sub.pending_plan == FREE_PLAN and period_end and now >= period_end:
            await _end_paid_plan(db, sub)
        elif sub.status == "active" and period_end and now >= period_end + grace:
            # The renewal (or the scheduled cheaper plan) was never confirmed by Paystack.
            await _end_paid_plan(db, sub)
            if user:
                email_service.send_plan_ended(user.email, name, plan_name)
        elif sub.status == "past_due" and user and (
            sub.last_payment_reminder_at is None or now - sub.last_payment_reminder_at >= timedelta(days=1)
        ):
            grace_ends = (sub.payment_failed_at or now) + grace
            days_left = max(1, math.ceil((grace_ends - now).total_seconds() / 86400))
            email_service.send_payment_failed(user.email, name, plan_name, days_left)
            sub.last_payment_reminder_at = now
        await db.commit()
