"""Background sender for queued WhatsApp messages.

Messages are a database-backed queue rather than in-request loops, so a bulk
send survives a restart, respects Meta's rate limits, and can be retried per
recipient. The loop follows the same asyncio.create_task pattern as the existing
birthday and follow-up loops in app/main.py.

Concurrency note: claim_batch uses SELECT ... FOR UPDATE SKIP LOCKED, so several
Postgres workers can run safely. SQLAlchemy silently drops the clause on SQLite,
which is fine because SQLite is the single-process local-dev fallback. Do not run
multiple processes against SQLite.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import select, update, func

from app.database import AsyncSessionLocal
from app.models.customer import Customer
from app.models.user import User
from app.models.notification import Notification
from app.models.whatsapp_account import WhatsAppAccount, ACCOUNT_CONNECTED, ACCOUNT_ERROR
from app.models.whatsapp_campaign import (
    WhatsAppCampaign,
    CAMPAIGN_QUEUED,
    CAMPAIGN_SENDING,
    CAMPAIGN_COMPLETED,
    CAMPAIGN_FAILED,
)
from app.models.whatsapp_message import (
    WhatsAppMessage,
    MSG_TEMPLATE,
    MSG_TEXT,
    MSG_OPENER,
    MSG_QUEUED,
    MSG_SENDING,
    MSG_SENT,
    MSG_AWAITING_REPLY,
    MSG_REPLIED,
    MSG_FAILED,
    MSG_CANCELLED,
    MSG_EXPIRED,
    MSG_DELIVERED,
    MSG_READ,
    OPENER_EXPIRY_DAYS,
)
from app.models.whatsapp_contact import WhatsAppContact
from app.models.whatsapp_template import WhatsAppTemplate
from app.services.whatsapp_service import whatsapp_service, WhatsAppError
from app.utils import crypto

logger = logging.getLogger(__name__)

BATCH_SIZE = 50
MAX_ATTEMPTS = 5
PER_ACCOUNT_DELAY = 0.05   # ~20 messages/second, well under Meta's 80/s cap
STUCK_AFTER = timedelta(minutes=15)
# How far back to look for message activity when refreshing campaign counters.
RECOUNT_WINDOW = timedelta(minutes=30)
IDLE_SLEEP = 5
BUSY_SLEEP = 2


async def claim_batch(db, limit: int = BATCH_SIZE) -> List[int]:
    """Atomically move a batch of due messages into 'sending' and return their ids."""
    now = datetime.utcnow()
    stmt = (
        select(WhatsAppMessage)
        .where(
            WhatsAppMessage.status == MSG_QUEUED,
            (WhatsAppMessage.next_attempt_at.is_(None))
            | (WhatsAppMessage.next_attempt_at <= now),
        )
        .order_by(WhatsAppMessage.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    rows = (await db.execute(stmt)).scalars().all()
    ids = []
    for row in rows:
        row.status = MSG_SENDING
        row.updated_at = now
        ids.append(row.id)
    if ids:
        await db.commit()
    return ids


class _AccountContext:
    __slots__ = ("account", "token", "business_name", "error")

    def __init__(self, account, token, business_name, error=None):
        self.account = account
        self.token = token
        self.business_name = business_name
        self.error = error


async def _load_account_contexts(db, user_ids) -> Dict[int, _AccountContext]:
    contexts: Dict[int, _AccountContext] = {}
    accounts = (
        await db.execute(
            select(WhatsAppAccount).where(WhatsAppAccount.user_id.in_(list(user_ids)))
        )
    ).scalars().all()
    users = {
        u.id: u
        for u in (
            await db.execute(select(User).where(User.id.in_(list(user_ids))))
        ).scalars().all()
    }
    for account in accounts:
        user = users.get(account.user_id)
        business_name = (
            (user.business_name or user.full_name or "A business") if user else "A business"
        )
        if account.status != ACCOUNT_CONNECTED or not account.access_token_encrypted:
            contexts[account.user_id] = _AccountContext(
                account, None, business_name, error="WhatsApp is not connected."
            )
            continue
        try:
            token = crypto.decrypt(account.access_token_encrypted)
        except Exception:
            contexts[account.user_id] = _AccountContext(
                account, None, business_name,
                error="Stored WhatsApp credentials could not be read. Please reconnect.",
            )
            continue
        contexts[account.user_id] = _AccountContext(account, token, business_name)
    return contexts


async def _resolve_template_params(db, message: WhatsAppMessage, campaign) -> tuple:
    """Build the ordered parameter list for a template message."""
    if not campaign or not campaign.template_params:
        return [], None
    values = []
    for p in campaign.template_params:
        if (p.get("source") or "static") == "customer_name":
            values.append(message.customer_name or "there")
        else:
            values.append(p.get("value") or "")
    row = (
        await db.execute(
            select(WhatsAppTemplate).where(
                WhatsAppTemplate.user_id == message.user_id,
                WhatsAppTemplate.name == campaign.template_name,
                WhatsAppTemplate.language == campaign.template_language,
            )
        )
    ).scalar_one_or_none()
    return values, (row.body_param_names if row else None)


async def _send_one(db, message: WhatsAppMessage, ctx: _AccountContext, campaign) -> None:
    now = datetime.utcnow()
    account = ctx.account

    if ctx.error:
        message.status = MSG_FAILED
        message.error_message = ctx.error[:500]
        message.failed_at = now
        message.updated_at = now
        await db.commit()
        return

    try:
        if message.kind == MSG_TEMPLATE:
            params, param_names = await _resolve_template_params(db, message, campaign)
            wamid = await whatsapp_service.send_template(
                account.phone_number_id,
                ctx.token,
                message.to_phone,
                campaign.template_name,
                campaign.template_language,
                params=params,
                param_names=param_names,
            )
            message.status = MSG_SENT
        elif message.kind == MSG_OPENER:
            wamid = await whatsapp_service.send_template(
                account.phone_number_id,
                ctx.token,
                message.to_phone,
                account.opener_template_name,
                account.opener_template_language,
                params=[message.customer_name or "there", ctx.business_name],
            )
            # The real message stays parked until the customer replies.
            message.status = MSG_AWAITING_REPLY
            message.expires_at = now + timedelta(days=OPENER_EXPIRY_DAYS)
        else:
            wamid = await whatsapp_service.send_text(
                account.phone_number_id, ctx.token, message.to_phone, message.body or ""
            )
            message.status = MSG_SENT

        message.wa_message_id = wamid
        message.sent_at = now
        message.attempts += 1
        message.updated_at = now

        contact = (
            await db.execute(
                select(WhatsAppContact).where(
                    WhatsAppContact.user_id == message.user_id,
                    WhatsAppContact.phone_e164 == message.to_phone,
                )
            )
        ).scalar_one_or_none()
        if contact is None:
            contact = WhatsAppContact(
                user_id=message.user_id,
                phone_e164=message.to_phone,
                customer_id=message.customer_id,
            )
            db.add(contact)
        contact.last_outbound_at = now
        contact.updated_at = now
        await db.commit()

    except WhatsAppError as e:
        message.attempts += 1
        message.error_code = e.code
        message.error_message = str(e)[:500]
        message.updated_at = now

        if e.retryable and message.attempts < MAX_ATTEMPTS:
            backoff = min(300, 5 * (2 ** message.attempts))
            message.status = MSG_QUEUED
            message.next_attempt_at = now + timedelta(seconds=backoff)
        else:
            message.status = MSG_FAILED
            message.failed_at = now
        await db.commit()

        if e.auth or e.account_blocked:
            account.status = ACCOUNT_ERROR
            if e.auth:
                account.last_error = "WhatsApp connection expired. Please reconnect."
            else:
                account.last_error = (
                    f"WhatsApp has restricted this account: {e.message}. "
                    "This usually means business verification is incomplete."
                )[:500]
            account.updated_at = now
            await db.execute(
                update(WhatsAppMessage)
                .where(
                    WhatsAppMessage.user_id == message.user_id,
                    WhatsAppMessage.status.in_([MSG_QUEUED, MSG_SENDING]),
                )
                .values(
                    status=MSG_FAILED,
                    error_message="WhatsApp connection lost. Reconnect and try again.",
                    failed_at=now,
                    updated_at=now,
                )
            )
            await db.commit()
        elif e.template and campaign is not None:
            # The template itself is wrong, so every sibling message would fail.
            await db.execute(
                update(WhatsAppMessage)
                .where(
                    WhatsAppMessage.campaign_id == campaign.id,
                    WhatsAppMessage.status.in_([MSG_QUEUED, MSG_SENDING]),
                )
                .values(status=MSG_CANCELLED, updated_at=now)
            )
            campaign.status = CAMPAIGN_FAILED
            campaign.completed_at = now
            await db.commit()

    except Exception as e:
        # The failure may have been the success-path commit itself, which leaves
        # the session needing a rollback before anything else can be written.
        try:
            await db.rollback()
        except Exception:
            pass
        message.attempts += 1
        message.status = MSG_FAILED
        message.error_message = f"{type(e).__name__}: {e}"[:500]
        message.failed_at = now
        message.updated_at = now
        try:
            await db.commit()
        except Exception as commit_error:
            print(f"WhatsApp worker could not record failure: {commit_error}")
            await db.rollback()


async def process_batch(message_ids: List[int]) -> None:
    async with AsyncSessionLocal() as db:
        messages = (
            await db.execute(
                select(WhatsAppMessage).where(WhatsAppMessage.id.in_(message_ids))
            )
        ).scalars().all()
        if not messages:
            return

        contexts = await _load_account_contexts(db, {m.user_id for m in messages})

        campaign_ids = {m.campaign_id for m in messages if m.campaign_id}
        campaigns = {}
        if campaign_ids:
            campaigns = {
                c.id: c
                for c in (
                    await db.execute(
                        select(WhatsAppCampaign).where(WhatsAppCampaign.id.in_(campaign_ids))
                    )
                ).scalars().all()
            }
            for c in campaigns.values():
                if c.status == CAMPAIGN_QUEUED:
                    c.status = CAMPAIGN_SENDING
                    c.started_at = c.started_at or datetime.utcnow()
            await db.commit()

        # Sends run one at a time against the shared session, which is not
        # concurrency-safe. The pause between sends keeps each account well under
        # Meta's per-number throughput limit (~20/s here against a cap of 80/s).
        for message in messages:
            ctx = contexts.get(message.user_id)
            if ctx is None:
                message.status = MSG_FAILED
                message.error_message = "WhatsApp is not connected."
                message.failed_at = datetime.utcnow()
                message.updated_at = datetime.utcnow()
                await db.commit()
                continue
            # Refresh the claim before each send. Without this a long batch
            # looks abandoned to the reaper, which would re-send in-flight
            # messages (and bill the vendor twice) on a second worker.
            message.updated_at = datetime.utcnow()
            await db.commit()
            await _send_one(db, message, ctx, campaigns.get(message.campaign_id))
            await asyncio.sleep(PER_ACCOUNT_DELAY)


async def reap_stuck(db) -> int:
    """Return messages abandoned mid-send (process died) to the queue."""
    cutoff = datetime.utcnow() - STUCK_AFTER
    result = await db.execute(
        update(WhatsAppMessage)
        .where(WhatsAppMessage.status == MSG_SENDING, WhatsAppMessage.updated_at < cutoff)
        .values(status=MSG_QUEUED, next_attempt_at=datetime.utcnow())
    )
    if result.rowcount:
        await db.commit()
    return result.rowcount or 0


async def expire_openers(db) -> int:
    """Give up on parked messages whose customer never replied."""
    now = datetime.utcnow()
    result = await db.execute(
        update(WhatsAppMessage)
        .where(
            WhatsAppMessage.status == MSG_AWAITING_REPLY,
            WhatsAppMessage.expires_at.isnot(None),
            WhatsAppMessage.expires_at < now,
        )
        .values(status=MSG_EXPIRED, updated_at=now)
    )
    if result.rowcount:
        await db.commit()
    return result.rowcount or 0


async def finalize_campaigns(db) -> None:
    """Recompute counters and close out campaigns with no work left."""
    # Campaigns still working, plus any campaign whose messages changed recently.
    # A custom campaign finishes sending its openers long before the replies
    # arrive, so counters must keep moving after it is marked completed, and one
    # final recount has to run once the last message settles.
    recent_cutoff = datetime.utcnow() - RECOUNT_WINDOW
    recently_touched = (
        await db.execute(
            select(WhatsAppMessage.campaign_id)
            .where(
                WhatsAppMessage.campaign_id.isnot(None),
                (WhatsAppMessage.status.in_([MSG_QUEUED, MSG_SENDING, MSG_AWAITING_REPLY]))
                | (WhatsAppMessage.updated_at >= recent_cutoff),
            )
            .distinct()
        )
    ).scalars().all()

    active = (
        await db.execute(
            select(WhatsAppCampaign).where(
                (WhatsAppCampaign.status.in_([CAMPAIGN_QUEUED, CAMPAIGN_SENDING]))
                | (WhatsAppCampaign.id.in_(recently_touched))
            )
        )
    ).scalars().all()
    if not active:
        return

    now = datetime.utcnow()
    for campaign in active:
        rows = (
            await db.execute(
                select(WhatsAppMessage.status, func.count())
                .where(WhatsAppMessage.campaign_id == campaign.id)
                .group_by(WhatsAppMessage.status)
            )
        ).all()
        counts = {status: count for status, count in rows}

        campaign.sent_count = (
            counts.get(MSG_SENT, 0) + counts.get(MSG_DELIVERED, 0) + counts.get(MSG_READ, 0)
        )
        campaign.delivered_count = counts.get(MSG_DELIVERED, 0) + counts.get(MSG_READ, 0)
        campaign.read_count = counts.get(MSG_READ, 0)
        campaign.awaiting_count = counts.get(MSG_AWAITING_REPLY, 0)
        campaign.replied_count = counts.get(MSG_REPLIED, 0)
        campaign.failed_count = counts.get(MSG_FAILED, 0)
        campaign.expired_count = counts.get(MSG_EXPIRED, 0)

        outstanding = counts.get(MSG_QUEUED, 0) + counts.get(MSG_SENDING, 0)
        already_completed = campaign.status == CAMPAIGN_COMPLETED
        if outstanding == 0 and not already_completed:
            campaign.status = CAMPAIGN_COMPLETED
            campaign.completed_at = campaign.completed_at or now
            db.add(
                Notification(
                    user_id=campaign.user_id,
                    type="check",
                    title="WhatsApp campaign completed",
                    names=(
                        f"{campaign.sent_count} sent"
                        + (
                            f", {campaign.awaiting_count} awaiting reply"
                            if campaign.awaiting_count
                            else ""
                        )
                        + (f", {campaign.failed_count} failed" if campaign.failed_count else "")
                    ),
                )
            )
    await db.commit()


async def whatsapp_send_loop() -> None:
    print("WhatsApp send worker started")
    while True:
        claimed: List[int] = []
        try:
            async with AsyncSessionLocal() as db:
                await reap_stuck(db)
                await expire_openers(db)
                claimed = await claim_batch(db)

            if claimed:
                await process_batch(claimed)

            async with AsyncSessionLocal() as db:
                await finalize_campaigns(db)
        except Exception as e:
            print(f"WhatsApp worker error: {type(e).__name__}: {e}")
        await asyncio.sleep(BUSY_SLEEP if claimed else IDLE_SLEEP)
