import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user_id
from app.models.user import User
from app.rate_limit import RateLimitedRouter
from app.services import billing_service
from app.services.paystack_service import paystack_service

logger = logging.getLogger(__name__)

router = RateLimitedRouter(prefix="/api/billing", tags=["billing"], limit="30/minute")
# No auth: the landing page reads the plans, and Paystack calls the webhook.
public_router = APIRouter(prefix="/api/billing", tags=["billing"])

APP_BASE_URL = os.getenv("APP_BASE_URL", "https://mycareloop.com.ng").rstrip("/")


class ChangePlanRequest(BaseModel):
    plan: str
    meta_fees_acknowledged: bool = False


@router.get("/status")
async def get_billing_status(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await billing_service.billing_status(db, user_id)


@router.post("/change-plan")
async def change_plan(
    request: Request,
    body: ChangePlanRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Upgrades return a Paystack checkout URL; downgrades and cancellations are
    scheduled for the end of the paid month."""
    user = await db.get(User, user_id)
    return await billing_service.change_plan(
        db, user, body.plan,
        callback_url=f"{APP_BASE_URL}/dashboard?page=billing&checkout=return",
        cancel_url=f"{APP_BASE_URL}/dashboard?page=billing",
        meta_fees_acknowledged=body.meta_fees_acknowledged,
    )


@router.get("/card-update-link")
async def get_card_update_link(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return {"link": await billing_service.card_update_link(db, user_id)}


@public_router.get("/plans")
async def get_plans():
    return {"plans": billing_service.public_plans()}


@public_router.post("/paystack/webhook")
async def paystack_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    raw = await request.body()
    if not paystack_service.verify_signature(raw, request.headers.get("x-paystack-signature")):
        raise HTTPException(status_code=401, detail="Invalid signature")
    try:
        event = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    try:
        await billing_service.handle_webhook(db, event)
    except Exception as e:
        # A non-200 answer makes Paystack send the event again later.
        logger.exception(f"PAYSTACK WEBHOOK ERROR on {event.get('event')}: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail="Could not process event")
    return {"status": "ok"}
