"""WhatsApp Business endpoints: connection, templates, campaigns and webhook."""
import json
import logging
from typing import Optional, List

from fastapi import Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user_id
from app.rate_limit import RateLimitedRouter, limiter
from app.controllers.whatsapp_controller import WhatsAppController
from app.services.whatsapp_service import whatsapp_service
from app.services.audit_service import log_action
from app.schemas.whatsapp import (
    WhatsAppStatusResponse,
    ConnectRequest,
    ManualConnectRequest,
    TemplateResponse,
    CampaignPreviewRequest,
    CampaignPreviewResponse,
    CampaignCreateRequest,
    CampaignSummary,
    CampaignListResponse,
    CampaignDetailResponse,
    SingleSendRequest,
    SingleSendResponse,
    TestSendRequest,
)

logger = logging.getLogger(__name__)

router = RateLimitedRouter(prefix="/api/whatsapp", tags=["whatsapp"], limit="30/minute")


# --- connection -------------------------------------------------------------

@router.get("/status", response_model=WhatsAppStatusResponse)
async def whatsapp_status(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    account = await WhatsAppController.get_account(db, user_id)
    return WhatsAppController.status_payload(account)


@router.post("/connect", response_model=WhatsAppStatusResponse)
@limiter.limit("5/minute")
async def whatsapp_connect(
    request: Request,
    data: ConnectRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await WhatsAppController.connect(
        db, user_id, data.code, data.waba_id, data.phone_number_id
    )
    await log_action(
        db,
        action="whatsapp_connect",
        resource="whatsapp_account",
        user_id=user_id,
        detail=f"waba={data.waba_id}",
    )
    return result


@router.post("/connect/manual", response_model=WhatsAppStatusResponse)
@limiter.limit("5/minute")
async def whatsapp_connect_manual(
    request: Request,
    data: ManualConnectRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Development-only path; returns 404 unless WHATSAPP_ALLOW_MANUAL_CONNECT is on."""
    result = await WhatsAppController.manual_connect(
        db, user_id, data.access_token, data.waba_id, data.phone_number_id
    )
    await log_action(
        db,
        action="whatsapp_connect_manual",
        resource="whatsapp_account",
        user_id=user_id,
        detail=f"waba={data.waba_id}",
    )
    return result


@router.delete("/disconnect")
async def whatsapp_disconnect(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await WhatsAppController.disconnect(db, user_id)
    await log_action(
        db, action="whatsapp_disconnect", resource="whatsapp_account", user_id=user_id
    )
    return result


@router.post("/opener-template/ensure")
@limiter.limit("10/minute")
async def ensure_opener_template(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await WhatsAppController.ensure_opener_template(db, user_id)


# --- templates --------------------------------------------------------------

@router.get("/templates", response_model=List[TemplateResponse])
@limiter.limit("20/minute")
async def list_templates(
    request: Request,
    refresh: bool = False,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    from app.controllers.whatsapp_controller import template_support

    rows = await WhatsAppController.list_templates(db, user_id, refresh=refresh)
    out = []
    for row in rows:
        supported, reason = template_support(row)
        item = TemplateResponse.model_validate(row).model_dump()
        item["supported"] = supported
        item["unsupported_reason"] = reason
        out.append(item)
    return out


# --- campaigns --------------------------------------------------------------

@router.post("/campaigns/preview", response_model=CampaignPreviewResponse)
async def preview_campaign(
    request: Request,
    data: CampaignPreviewRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await WhatsAppController.preview_campaign(
        db, user_id, data.kind, data.audience, data.customer_ids
    )


@router.post("/campaigns", response_model=CampaignSummary, status_code=201)
@limiter.limit("5/minute")
async def create_campaign(
    request: Request,
    data: CampaignCreateRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    campaign = await WhatsAppController.create_campaign(db, user_id, data)
    await log_action(
        db,
        action="whatsapp_campaign_create",
        resource="whatsapp_campaign",
        user_id=user_id,
        resource_id=campaign.id,
        detail=(
            f"kind={campaign.kind} recipients={campaign.total_recipients} "
            f"direct={campaign.direct_count} opener={campaign.opener_count}"
        ),
    )
    return campaign


@router.get("/campaigns", response_model=CampaignListResponse)
async def list_campaigns(
    request: Request,
    page: int = 1,
    per_page: int = 20,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await WhatsAppController.list_campaigns(db, user_id, page, per_page)


@router.get("/campaigns/{campaign_id}", response_model=CampaignDetailResponse)
async def get_campaign(
    request: Request,
    campaign_id: int,
    page: int = 1,
    per_page: int = 100,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await WhatsAppController.get_campaign(db, user_id, campaign_id, page, per_page)


@router.post("/campaigns/{campaign_id}/cancel")
async def cancel_campaign(
    request: Request,
    campaign_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await WhatsAppController.cancel_campaign(db, user_id, campaign_id)
    await log_action(
        db,
        action="whatsapp_campaign_cancel",
        resource="whatsapp_campaign",
        user_id=user_id,
        resource_id=campaign_id,
    )
    return result


# --- single / test sends ----------------------------------------------------

@router.post("/send", response_model=SingleSendResponse)
@limiter.limit("30/minute")
async def send_single(
    request: Request,
    data: SingleSendRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await WhatsAppController.single_send(db, user_id, data.customer_id, data.text_body)


@router.post("/send-test")
@limiter.limit("10/minute")
async def send_test(
    request: Request,
    data: TestSendRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await WhatsAppController.send_test(db, user_id, data)


# --- webhook (unauthenticated; secured by verify token + HMAC signature) -----

@router.get("/webhook")
async def verify_webhook(request: Request):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if mode == "subscribe" and whatsapp_service.verify_token_matches(token):
        return PlainTextResponse(content=challenge or "")
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook")
async def receive_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    raw = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    if not whatsapp_service.verify_webhook_signature(raw, signature):
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return {"ok": True}

    # Always answer 200 once the signature checks out; a non-200 makes Meta
    # retry and eventually disable the subscription.
    try:
        await WhatsAppController.handle_webhook(db, payload)
    except Exception as e:
        print(f"WA WEBHOOK ERROR: {type(e).__name__}: {e}")
    return {"ok": True}
