"""Business logic for the WhatsApp Business integration.

Two campaign kinds are supported:

  * template -- an approved Meta template sent to everyone.
  * custom   -- arbitrary text. Meta only allows free-form text within 24 hours
                of a customer's last inbound message, so recipients outside that
                window are sent an approved opener template first and the real
                message is parked until they reply (see handle_webhook).
"""
import json
import re
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple

from fastapi import HTTPException, status as http_status
from sqlalchemy import select, delete, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.models.user import User
from app.models.notification import Notification
from app.models.whatsapp_account import (
    WhatsAppAccount,
    ACCOUNT_CONNECTED,
    ACCOUNT_ERROR,
    ACCOUNT_DISCONNECTED,
)
from app.models.whatsapp_template import WhatsAppTemplate, TEMPLATE_APPROVED
from app.models.whatsapp_campaign import (
    WhatsAppCampaign,
    KIND_TEMPLATE,
    KIND_CUSTOM,
    CAMPAIGN_QUEUED,
    CAMPAIGN_SENDING,
    CAMPAIGN_COMPLETED,
    CAMPAIGN_CANCELLED,
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
    MSG_DELIVERED,
    MSG_READ,
    MSG_AWAITING_REPLY,
    MSG_REPLIED,
    MSG_FAILED,
    MSG_CANCELLED,
    MSG_EXPIRED,
    STATUS_RANK,
    OPENER_EXPIRY_DAYS,
)
from app.models.whatsapp_contact import (
    WhatsAppContact,
    OPT_OUT_INBOUND,
    SERVICE_WINDOW_HOURS,
)
from app.services.whatsapp_service import (
    whatsapp_service,
    WhatsAppError,
    OPENER_TEMPLATE_NAME,
    OPENER_TEMPLATE_LANGUAGE,
    OPENER_TEMPLATE_BODY,
)
from app.utils import crypto
from app.utils.phone import normalize_phone

logger = logging.getLogger(__name__)

STOP_WORDS = re.compile(r"^\s*(stop|unsubscribe|opt\s*out|optout|cancel|end|quit)\s*[.!]?\s*$", re.I)
START_WORDS = re.compile(r"^\s*(start|unstop|subscribe|resume)\s*[.!]?\s*$", re.I)

TEMPLATE_CACHE_TTL = timedelta(hours=6)
_PARAM_TOKEN = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")



# Templates Careloop can fill completely. Media headers and dynamic buttons need
# parameters this version does not collect, and Meta rejects the send with 132012
# rather than substituting a default, so they must not be offered in the picker.
SUPPORTED_HEADER_TYPES = (None, "TEXT")


def template_support(row) -> tuple:
    """Return (is_supported, reason) for a cached template row."""
    header = (row.header_type or None)
    if header not in SUPPORTED_HEADER_TYPES:
        word = header.lower()
        article = "an" if word[:1] in "aeiou" else "a"
        return False, f"needs {article} {word} attachment"

    components = ((row.components or {}).get("components")) or []
    for comp in components:
        ctype = (comp.get("type") or "").upper()
        if ctype == "HEADER" and _PARAM_TOKEN.search(comp.get("text") or ""):
            return False, "has a variable in its header"
        if ctype == "CAROUSEL":
            return False, "is a carousel"
        if ctype in ("LIMITED_TIME_OFFER", "LIMITED_TIME_OFFER_EXPIRATION"):
            return False, "is a limited-time offer"
        if ctype == "BUTTONS":
            for btn in comp.get("buttons") or []:
                btype = (btn.get("type") or "").upper()
                if btype == "URL" and _PARAM_TOKEN.search(btn.get("url") or ""):
                    return False, "has a button with a variable link"
                if btype == "COPY_CODE":
                    return False, "has a copy-code button"
    return True, None


class WhatsAppController:

    # ------------------------------------------------------------------
    # account helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def get_account(db: AsyncSession, user_id: int) -> Optional[WhatsAppAccount]:
        result = await db.execute(
            select(WhatsAppAccount).where(WhatsAppAccount.user_id == user_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def require_connected_account(db: AsyncSession, user_id: int) -> WhatsAppAccount:
        account = await WhatsAppController.get_account(db, user_id)
        if not account or not account.is_connected:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail="WhatsApp is not connected. Connect it in Account Settings first.",
            )
        return account

    @staticmethod
    def token_for(account: WhatsAppAccount) -> str:
        try:
            return crypto.decrypt(account.access_token_encrypted)
        except crypto.EncryptionUnavailable as e:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail=f"WhatsApp connection is unusable: {e}. Please reconnect.",
            )

    @staticmethod
    def status_payload(account: Optional[WhatsAppAccount]) -> dict:
        base = {
            "enabled": whatsapp_service.enabled,
            "connected": False,
            "opener_preview": OPENER_TEMPLATE_BODY,
        }
        if not account:
            return base
        base.update(
            {
                "connected": account.is_connected,
                "status": account.status,
                "display_phone_number": account.display_phone_number,
                "verified_name": account.verified_name,
                "quality_rating": account.quality_rating,
                "waba_id": account.waba_id,
                "phone_number_id": account.phone_number_id,
                "templates_synced_at": account.templates_synced_at,
                "opener_template_status": account.opener_template_status,
                "connected_at": account.connected_at,
                "last_error": account.last_error,
            }
        )
        return base

    # ------------------------------------------------------------------
    # connect / disconnect
    # ------------------------------------------------------------------

    @staticmethod
    async def connect(
        db: AsyncSession,
        user_id: int,
        code: str,
        waba_id: str,
        phone_number_id: str,
    ) -> dict:
        if not whatsapp_service.enabled:
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="WhatsApp integration is not configured on this server.",
            )
        try:
            token = await whatsapp_service.exchange_code(code)
            token_data = await whatsapp_service.debug_token(token)
        except WhatsAppError as e:
            raise HTTPException(status_code=400, detail=f"Could not complete WhatsApp setup: {e}")

        if not whatsapp_service.token_issued_to_this_app(token_data):
            raise HTTPException(
                status_code=403, detail="This login did not come from Careloop."
            )
        # Authoritative: Meta only returns the account to a token that holds it,
        # so this blocks pairing a valid token with someone else's WABA id.
        if not await whatsapp_service.verify_waba_access(waba_id, token):
            raise HTTPException(
                status_code=403,
                detail="This WhatsApp Business Account was not granted to Careloop.",
            )
        return await WhatsAppController._finish_connect(
            db, user_id, token, waba_id, phone_number_id
        )

    @staticmethod
    async def manual_connect(
        db: AsyncSession, user_id: int, access_token: str, waba_id: str, phone_number_id: str
    ) -> dict:
        """Development path: paste a token from Meta's API Setup screen."""
        if not whatsapp_service.allow_manual_connect:
            raise HTTPException(status_code=404, detail="Not found")
        if not crypto.is_available():
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="WHATSAPP_TOKEN_ENCRYPTION_KEY is not configured.",
            )
        if not await whatsapp_service.verify_waba_access(waba_id, access_token):
            raise HTTPException(
                status_code=403,
                detail="That token does not have access to this WhatsApp Business Account.",
            )
        return await WhatsAppController._finish_connect(
            db, user_id, access_token, waba_id, phone_number_id
        )

    @staticmethod
    async def _finish_connect(
        db: AsyncSession, user_id: int, token: str, waba_id: str, phone_number_id: str
    ) -> dict:
        subscribed = False
        registered = False
        warnings: List[str] = []

        try:
            await whatsapp_service.subscribe_app(waba_id, token)
            subscribed = True
        except WhatsAppError as e:
            warnings.append(f"Webhook subscription failed: {e}")

        try:
            await whatsapp_service.register_phone(phone_number_id, token)
            registered = True
        except WhatsAppError as e:
            # Already-registered numbers report an error that is safe to ignore.
            if e.code in (133005, 133010, 133015) or "already" in (e.message or "").lower():
                registered = True
            else:
                warnings.append(f"Phone registration failed: {e}")

        display_phone = verified_name = quality = None
        try:
            info = await whatsapp_service.get_phone_number(phone_number_id, token)
            display_phone = info.get("display_phone_number")
            verified_name = info.get("verified_name")
            quality = info.get("quality_rating")
        except WhatsAppError as e:
            warnings.append(f"Could not read phone details: {e}")

        account = await WhatsAppController.get_account(db, user_id)
        if account is None:
            account = WhatsAppAccount(user_id=user_id)
            db.add(account)
        elif account.waba_id != waba_id:
            # A different business account has no opener template of its own, so
            # the previous approval must not be carried over.
            account.opener_template_status = None

        account.waba_id = waba_id
        account.phone_number_id = phone_number_id
        account.display_phone_number = display_phone
        account.verified_name = verified_name
        account.quality_rating = quality
        account.access_token_encrypted = crypto.encrypt(token)
        account.token_issued_at = datetime.utcnow()
        account.status = ACCOUNT_CONNECTED
        account.last_error = "; ".join(warnings)[:500] if warnings else None
        account.app_subscribed = subscribed
        account.phone_registered = registered
        account.opener_template_name = OPENER_TEMPLATE_NAME
        account.opener_template_language = OPENER_TEMPLATE_LANGUAGE
        account.connected_at = datetime.utcnow()
        account.disconnected_at = None
        account.updated_at = datetime.utcnow()

        await db.commit()
        await db.refresh(account)

        # Submit the opener template so custom messaging works. Non-fatal.
        await WhatsAppController.ensure_opener_template(db, user_id, account=account, token=token)
        await db.refresh(account)
        return WhatsAppController.status_payload(account)

    @staticmethod
    async def ensure_opener_template(
        db: AsyncSession,
        user_id: int,
        account: Optional[WhatsAppAccount] = None,
        token: Optional[str] = None,
    ) -> dict:
        account = account or await WhatsAppController.require_connected_account(db, user_id)
        token = token or WhatsAppController.token_for(account)

        # An APPROVED opener needs no resubmission.
        if account.opener_template_status == TEMPLATE_APPROVED:
            return {"status": TEMPLATE_APPROVED}

        create_error: Optional[WhatsAppError] = None
        reported = None
        try:
            result = await whatsapp_service.create_opener_template(account.waba_id, token)
            reported = (result or {}).get("status")
        except WhatsAppError as e:
            create_error = e

        # Creation failing usually means the template is already on the account.
        # Meta reports a duplicate inconsistently (132001, or a generic code 100
        # "Invalid parameter"), so rather than matching error codes, ask the
        # template list what actually exists. That is authoritative either way.
        if create_error is not None or reported == "EXISTS":
            try:
                await WhatsAppController.sync_templates(
                    db, user_id, account=account, token=token
                )
                await db.refresh(account)
            except Exception:
                pass
            if account.opener_template_status:
                account.last_error = None
                account.updated_at = datetime.utcnow()
                await db.commit()
                return {"status": account.opener_template_status}
            if create_error is not None:
                if create_error.template:
                    account.opener_template_status = "REJECTED"
                account.last_error = f"Intro message setup failed: {create_error}"[:500]
        else:
            account.opener_template_status = reported or "PENDING"
            account.last_error = None

        account.updated_at = datetime.utcnow()
        await db.commit()
        return {"status": account.opener_template_status}

    @staticmethod
    async def disconnect(db: AsyncSession, user_id: int) -> dict:
        account = await WhatsAppController.get_account(db, user_id)
        if not account:
            return {"success": True}

        if account.access_token_encrypted and account.waba_id:
            try:
                token = crypto.decrypt(account.access_token_encrypted)
                await whatsapp_service.unsubscribe_app(account.waba_id, token)
            except Exception as e:
                logger.info(f"WhatsApp unsubscribe skipped: {type(e).__name__}")

        # Stop anything still in flight.
        await db.execute(
            update(WhatsAppMessage)
            .where(
                WhatsAppMessage.user_id == user_id,
                WhatsAppMessage.status.in_([MSG_QUEUED, MSG_SENDING, MSG_AWAITING_REPLY]),
            )
            .values(status=MSG_CANCELLED, updated_at=datetime.utcnow())
        )
        await db.execute(
            update(WhatsAppCampaign)
            .where(
                WhatsAppCampaign.user_id == user_id,
                WhatsAppCampaign.status.in_([CAMPAIGN_QUEUED, CAMPAIGN_SENDING]),
            )
            .values(status=CAMPAIGN_CANCELLED, completed_at=datetime.utcnow())
        )

        account.access_token_encrypted = None
        account.status = ACCOUNT_DISCONNECTED
        account.disconnected_at = datetime.utcnow()
        account.updated_at = datetime.utcnow()
        account.opener_template_status = None
        await db.commit()
        return {"success": True}

    @staticmethod
    async def mark_account_error(db: AsyncSession, account: WhatsAppAccount, message: str):
        account.status = ACCOUNT_ERROR
        account.last_error = message[:500]
        account.updated_at = datetime.utcnow()
        await db.commit()

    # ------------------------------------------------------------------
    # templates
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_template(raw: dict) -> dict:
        """Pull the body text and its parameter list out of a Graph template."""
        body_text = None
        header_type = None
        for comp in raw.get("components") or []:
            ctype = (comp.get("type") or "").upper()
            if ctype == "BODY" and body_text is None:
                body_text = comp.get("text")
            elif ctype == "HEADER":
                header_type = (comp.get("format") or "TEXT").upper()

        tokens = _PARAM_TOKEN.findall(body_text or "")
        param_format = (raw.get("parameter_format") or "POSITIONAL").upper()
        if param_format == "NAMED":
            names, seen = [], set()
            for t in tokens:
                if t not in seen:
                    seen.add(t)
                    names.append(t)
            count = len(names)
        else:
            names = None
            numeric = {int(t) for t in tokens if t.isdigit()}
            count = max(numeric) if numeric else 0

        return {
            "body_text": body_text,
            "header_type": header_type,
            "parameter_format": param_format,
            "body_param_count": count,
            "body_param_names": names,
        }

    @staticmethod
    async def sync_templates(
        db: AsyncSession,
        user_id: int,
        account: Optional[WhatsAppAccount] = None,
        token: Optional[str] = None,
    ) -> List[WhatsAppTemplate]:
        account = account or await WhatsAppController.require_connected_account(db, user_id)
        token = token or WhatsAppController.token_for(account)

        try:
            remote = await whatsapp_service.list_templates(account.waba_id, token)
        except WhatsAppError as e:
            if e.auth:
                await WhatsAppController.mark_account_error(
                    db, account, "WhatsApp connection expired. Please reconnect."
                )
            raise HTTPException(status_code=502, detail=f"Could not load templates: {e}")

        existing = {
            (t.name, t.language): t
            for t in (
                await db.execute(
                    select(WhatsAppTemplate).where(WhatsAppTemplate.user_id == user_id)
                )
            ).scalars().all()
        }

        now = datetime.utcnow()
        seen = set()
        for raw in remote:
            name, language = raw.get("name"), raw.get("language")
            if not name or not language:
                continue
            seen.add((name, language))
            parsed = WhatsAppController._parse_template(raw)
            row = existing.get((name, language))
            if row is None:
                row = WhatsAppTemplate(user_id=user_id, name=name, language=language)
                db.add(row)
            row.waba_id = account.waba_id
            row.meta_template_id = str(raw.get("id")) if raw.get("id") else None
            row.category = raw.get("category")
            row.status = raw.get("status")
            row.components = {"components": raw.get("components")}
            row.synced_at = now
            for k, v in parsed.items():
                setattr(row, k, v)

            if name == account.opener_template_name and language == account.opener_template_language:
                account.opener_template_status = raw.get("status")

        # Drop templates the vendor deleted on Meta's side.
        for (name, language), row in existing.items():
            if (name, language) not in seen:
                await db.delete(row)

        account.templates_synced_at = now
        account.updated_at = now
        await db.commit()

        result = await db.execute(
            select(WhatsAppTemplate)
            .where(WhatsAppTemplate.user_id == user_id)
            .order_by(WhatsAppTemplate.name)
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_templates(
        db: AsyncSession, user_id: int, refresh: bool = False
    ) -> List[WhatsAppTemplate]:
        account = await WhatsAppController.require_connected_account(db, user_id)
        stale = (
            account.templates_synced_at is None
            or datetime.utcnow() - account.templates_synced_at > TEMPLATE_CACHE_TTL
        )
        if refresh or stale:
            return await WhatsAppController.sync_templates(db, user_id, account=account)

        result = await db.execute(
            select(WhatsAppTemplate)
            .where(WhatsAppTemplate.user_id == user_id)
            .order_by(WhatsAppTemplate.name)
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # recipient resolution
    # ------------------------------------------------------------------

    @staticmethod
    async def _load_contacts(
        db: AsyncSession, user_id: int, phones: List[str]
    ) -> Dict[str, WhatsAppContact]:
        contacts: Dict[str, WhatsAppContact] = {}
        if not phones:
            return contacts
        # Chunked so a large customer list never blows past parameter limits.
        for i in range(0, len(phones), 500):
            chunk = phones[i : i + 500]
            rows = (
                await db.execute(
                    select(WhatsAppContact).where(
                        WhatsAppContact.user_id == user_id,
                        WhatsAppContact.phone_e164.in_(chunk),
                    )
                )
            ).scalars().all()
            for row in rows:
                contacts[row.phone_e164] = row
        return contacts

    @staticmethod
    async def resolve_recipients(
        db: AsyncSession,
        user_id: int,
        audience: str,
        customer_ids: Optional[List[int]],
    ) -> Tuple[List[dict], Dict[str, int], int]:
        """Return (recipients, skipped counts, total selected).

        Each recipient is {customer, phone, in_window}. Audience is always
        resolved server side because the dashboard only ever holds 100 customers.
        """
        query = select(Customer).where(Customer.user_id == user_id)
        if audience == "selected":
            if not customer_ids:
                return [], {"no_phone": 0, "invalid_phone": 0, "opted_out": 0}, 0
            query = query.where(Customer.id.in_(customer_ids))

        customers = (await db.execute(query)).scalars().all()
        total_selected = len(customers)
        skipped = {"no_phone": 0, "invalid_phone": 0, "opted_out": 0}

        normalized: List[Tuple[Customer, str]] = []
        seen_phones = set()
        for customer in customers:
            if not customer.phone_number or not str(customer.phone_number).strip():
                skipped["no_phone"] += 1
                continue
            phone = normalize_phone(customer.phone_number)
            if not phone:
                skipped["invalid_phone"] += 1
                continue
            if phone in seen_phones:
                continue  # same person listed twice; not a skip worth reporting
            seen_phones.add(phone)
            normalized.append((customer, phone))

        contacts = await WhatsAppController._load_contacts(
            db, user_id, [p for _, p in normalized]
        )

        now = datetime.utcnow()
        recipients: List[dict] = []
        for customer, phone in normalized:
            contact = contacts.get(phone)
            if contact and contact.opted_out:
                skipped["opted_out"] += 1
                continue
            recipients.append(
                {
                    "customer": customer,
                    "phone": phone,
                    "in_window": bool(contact and contact.in_service_window(now)),
                }
            )
        return recipients, skipped, total_selected

    @staticmethod
    async def preview_campaign(
        db: AsyncSession, user_id: int, kind: str, audience: str, customer_ids: Optional[List[int]]
    ) -> dict:
        await WhatsAppController.require_connected_account(db, user_id)
        recipients, skipped, total_selected = await WhatsAppController.resolve_recipients(
            db, user_id, audience, customer_ids
        )
        in_window = sum(1 for r in recipients if r["in_window"])
        # Template messages reach everyone; only custom text needs the opener.
        needs_opener = 0 if kind == KIND_TEMPLATE else len(recipients) - in_window
        return {
            "total_selected": total_selected,
            "eligible": len(recipients),
            "in_window": in_window if kind == KIND_CUSTOM else len(recipients),
            "needs_opener": needs_opener,
            "skipped": skipped,
            "sample": [
                {
                    "customer_id": r["customer"].id,
                    "name": r["customer"].name,
                    "to_phone": r["phone"],
                    "in_window": r["in_window"],
                }
                for r in recipients[:10]
            ],
        }

    # ------------------------------------------------------------------
    # campaigns
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_params(params: Optional[List[dict]], customer_name: str) -> List[str]:
        resolved = []
        for p in params or []:
            if (p.get("source") or "static") == "customer_name":
                resolved.append(customer_name or "there")
            else:
                resolved.append(p.get("value") or "")
        return resolved

    @staticmethod
    async def create_campaign(db: AsyncSession, user_id: int, data) -> dict:
        account = await WhatsAppController.require_connected_account(db, user_id)
        params = [p.model_dump() for p in (data.params or [])]
        template_row = None

        if data.kind == KIND_TEMPLATE:
            if not data.template_name or not data.template_language:
                raise HTTPException(status_code=400, detail="Choose a template to send.")
            template_row = (
                await db.execute(
                    select(WhatsAppTemplate).where(
                        WhatsAppTemplate.user_id == user_id,
                        WhatsAppTemplate.name == data.template_name,
                        WhatsAppTemplate.language == data.template_language,
                    )
                )
            ).scalar_one_or_none()
            if not template_row:
                raise HTTPException(status_code=404, detail="Template not found. Refresh templates.")
            if template_row.status != TEMPLATE_APPROVED:
                raise HTTPException(
                    status_code=409,
                    detail=f"Template '{template_row.name}' is {template_row.status or 'not approved'}.",
                )
            supported, reason = template_support(template_row)
            if not supported:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"'{template_row.name}' {reason}, which Careloop cannot send yet. "
                        "Choose a text-only template, or use the Custom message tab."
                    ),
                )
            if len(params) != template_row.body_param_count:
                raise HTTPException(
                    status_code=400,
                    detail=f"This template needs {template_row.body_param_count} value(s).",
                )
        else:
            if not data.custom_body or not data.custom_body.strip():
                raise HTTPException(status_code=400, detail="Write a message to send.")

        recipients, skipped, total_selected = await WhatsAppController.resolve_recipients(
            db, user_id, data.audience, data.customer_ids
        )
        if not recipients:
            raise HTTPException(
                status_code=400,
                detail="No eligible recipients. Customers need a full international phone number.",
            )

        # The opener is only needed for recipients outside the 24-hour window, so
        # a campaign aimed entirely at recent chats can go out without it.
        if data.kind == KIND_CUSTOM and account.opener_template_status != TEMPLATE_APPROVED:
            if any(not r["in_window"] for r in recipients):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Your intro message is still being approved by WhatsApp. "
                        "Until then you can only message customers who wrote to you "
                        "in the last 24 hours."
                    ),
                )

        now = datetime.utcnow()
        campaign = WhatsAppCampaign(
            user_id=user_id,
            account_id=account.id,
            name=data.name,
            kind=data.kind,
            audience=data.audience,
            template_name=data.template_name,
            template_language=data.template_language,
            template_params=params or None,
            custom_body=data.custom_body,
            status=CAMPAIGN_QUEUED,
            total_selected=total_selected,
            total_recipients=len(recipients),
            skipped=skipped,
            skipped_count=sum(skipped.values()),
            created_at=now,
        )
        db.add(campaign)
        await db.flush()

        direct = opener = 0
        for r in recipients:
            customer = r["customer"]
            if data.kind == KIND_TEMPLATE:
                kind, body = MSG_TEMPLATE, None
                direct += 1
            elif r["in_window"]:
                kind, body = MSG_TEXT, data.custom_body
                direct += 1
            else:
                # Parked: the opener goes out now, the real text after they reply.
                kind, body = MSG_OPENER, data.custom_body
                opener += 1
            db.add(
                WhatsAppMessage(
                    campaign_id=campaign.id,
                    user_id=user_id,
                    customer_id=customer.id,
                    customer_name=customer.name,
                    to_phone=r["phone"],
                    kind=kind,
                    body=body,
                    status=MSG_QUEUED,
                    next_attempt_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )

        campaign.direct_count = direct
        campaign.opener_count = opener
        await db.commit()
        await db.refresh(campaign)
        return campaign

    @staticmethod
    async def list_campaigns(
        db: AsyncSession, user_id: int, page: int = 1, per_page: int = 20
    ) -> dict:
        per_page = max(1, min(per_page, 100))
        total = (
            await db.execute(
                select(func.count())
                .select_from(WhatsAppCampaign)
                .where(WhatsAppCampaign.user_id == user_id)
            )
        ).scalar_one()
        rows = (
            await db.execute(
                select(WhatsAppCampaign)
                .where(WhatsAppCampaign.user_id == user_id)
                .order_by(WhatsAppCampaign.created_at.desc())
                .offset((page - 1) * per_page)
                .limit(per_page)
            )
        ).scalars().all()
        return {"items": list(rows), "total": total}

    @staticmethod
    async def get_campaign(
        db: AsyncSession, user_id: int, campaign_id: int, page: int = 1, per_page: int = 100
    ) -> dict:
        campaign = (
            await db.execute(
                select(WhatsAppCampaign).where(
                    WhatsAppCampaign.id == campaign_id,
                    WhatsAppCampaign.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        per_page = max(1, min(per_page, 500))
        total = (
            await db.execute(
                select(func.count())
                .select_from(WhatsAppMessage)
                .where(WhatsAppMessage.campaign_id == campaign_id)
            )
        ).scalar_one()
        messages = (
            await db.execute(
                select(WhatsAppMessage)
                .where(WhatsAppMessage.campaign_id == campaign_id)
                .order_by(WhatsAppMessage.id)
                .offset((page - 1) * per_page)
                .limit(per_page)
            )
        ).scalars().all()
        return {"campaign": campaign, "messages": list(messages), "messages_total": total}

    @staticmethod
    async def cancel_campaign(db: AsyncSession, user_id: int, campaign_id: int) -> dict:
        campaign = (
            await db.execute(
                select(WhatsAppCampaign).where(
                    WhatsAppCampaign.id == campaign_id,
                    WhatsAppCampaign.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        result = await db.execute(
            update(WhatsAppMessage)
            .where(
                WhatsAppMessage.campaign_id == campaign_id,
                WhatsAppMessage.status.in_([MSG_QUEUED, MSG_AWAITING_REPLY]),
            )
            .values(status=MSG_CANCELLED, updated_at=datetime.utcnow())
        )
        campaign.status = CAMPAIGN_CANCELLED
        campaign.completed_at = datetime.utcnow()
        await db.commit()
        return {"success": True, "cancelled": result.rowcount or 0}

    # ------------------------------------------------------------------
    # single + test sends (inline, not queued)
    # ------------------------------------------------------------------

    @staticmethod
    async def _upsert_contact(
        db: AsyncSession,
        user_id: int,
        phone: str,
        customer_id: Optional[int] = None,
        inbound_at: Optional[datetime] = None,
        outbound_at: Optional[datetime] = None,
    ) -> WhatsAppContact:
        contact = (
            await db.execute(
                select(WhatsAppContact).where(
                    WhatsAppContact.user_id == user_id,
                    WhatsAppContact.phone_e164 == phone,
                )
            )
        ).scalar_one_or_none()
        if contact is None:
            contact = WhatsAppContact(user_id=user_id, phone_e164=phone)
            db.add(contact)
        if customer_id and not contact.customer_id:
            contact.customer_id = customer_id
        if inbound_at:
            contact.last_inbound_at = inbound_at
        if outbound_at:
            contact.last_outbound_at = outbound_at
        contact.updated_at = datetime.utcnow()
        return contact

    @staticmethod
    async def _match_customer(db: AsyncSession, user_id: int, phone: str):
        """Find the customer behind an inbound number.

        Customer phone numbers are free text, so they are matched on their last
        nine digits (enough to be unique within one business) and then confirmed
        by full normalization. This avoids scanning the whole customer list.
        """
        tail = phone[-9:]
        candidates = (
            await db.execute(
                select(Customer).where(
                    Customer.user_id == user_id,
                    Customer.phone_number.isnot(None),
                    Customer.phone_number.like(f"%{tail}"),
                )
            )
        ).scalars().all()
        for c in candidates:
            if normalize_phone(c.phone_number) == phone:
                return c
        # Numbers stored with separators ("+234 803 123 4567") will not match the
        # plain tail, so try a pattern that tolerates characters between digits.
        spaced = "%" + "%".join(tail) + "%"
        loose = (
            await db.execute(
                select(Customer).where(
                    Customer.user_id == user_id,
                    Customer.phone_number.isnot(None),
                    Customer.phone_number.like(spaced),
                )
            )
        ).scalars().all()
        for c in loose:
            if normalize_phone(c.phone_number) == phone:
                return c
        return None

    @staticmethod
    async def single_send(
        db: AsyncSession, user_id: int, customer_id: int, text_body: str
    ) -> dict:
        account = await WhatsAppController.require_connected_account(db, user_id)
        token = WhatsAppController.token_for(account)

        customer = (
            await db.execute(
                select(Customer).where(
                    Customer.id == customer_id, Customer.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        phone = normalize_phone(customer.phone_number)
        if not phone:
            raise HTTPException(
                status_code=400,
                detail="This customer needs a full international phone number, e.g. +234 803 000 0000.",
            )

        contact = await WhatsAppController._upsert_contact(db, user_id, phone, customer.id)
        if contact.opted_out:
            raise HTTPException(
                status_code=409, detail="This customer opted out of WhatsApp messages."
            )

        now = datetime.utcnow()
        in_window = contact.in_service_window(now)

        if not in_window and account.opener_template_status != TEMPLATE_APPROVED:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This customer has not messaged you in the last 24 hours, and your "
                    "intro message is still awaiting WhatsApp approval."
                ),
            )

        message = WhatsAppMessage(
            user_id=user_id,
            customer_id=customer.id,
            customer_name=customer.name,
            to_phone=phone,
            kind=MSG_TEXT if in_window else MSG_OPENER,
            body=text_body,
            status=MSG_SENDING,
            created_at=now,
            updated_at=now,
        )
        db.add(message)
        await db.flush()

        business_name = await WhatsAppController._business_name(db, user_id)
        try:
            if in_window:
                wamid = await whatsapp_service.send_text(
                    account.phone_number_id, token, phone, text_body
                )
                message.status = MSG_SENT
            else:
                wamid = await whatsapp_service.send_template(
                    account.phone_number_id,
                    token,
                    phone,
                    account.opener_template_name,
                    account.opener_template_language,
                    params=[customer.name or "there", business_name],
                )
                message.status = MSG_AWAITING_REPLY
                message.expires_at = now + timedelta(days=OPENER_EXPIRY_DAYS)
            message.wa_message_id = wamid
            message.sent_at = now
            contact.last_outbound_at = now
            customer.last_contact = now
            await db.commit()
            return {
                "status": message.status,
                "wa_message_id": wamid,
                "message": (
                    "Message sent."
                    if in_window
                    else "Intro sent. Your message is delivered as soon as they reply."
                ),
            }
        except WhatsAppError as e:
            message.status = MSG_FAILED
            message.error_code = e.code
            message.error_message = str(e)[:500]
            message.failed_at = now
            await db.commit()
            if e.auth:
                await WhatsAppController.mark_account_error(
                    db, account, "WhatsApp connection expired. Please reconnect."
                )
            raise HTTPException(status_code=502, detail=f"WhatsApp could not send this: {e}")

    @staticmethod
    async def _business_name(db: AsyncSession, user_id: int) -> str:
        user = (
            await db.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        return (user.business_name or user.full_name or "A business") if user else "A business"

    @staticmethod
    async def send_test(db: AsyncSession, user_id: int, data) -> dict:
        account = await WhatsAppController.require_connected_account(db, user_id)
        token = WhatsAppController.token_for(account)

        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        raw_phone = data.to_phone or (user.phone if user else None)
        phone = normalize_phone(raw_phone)
        if not phone:
            raise HTTPException(
                status_code=400,
                detail="Enter a full international phone number to send the test to.",
            )

        business_name = (user.business_name or user.full_name or "A business") if user else "A business"
        try:
            if data.kind == KIND_TEMPLATE:
                if not data.template_name or not data.template_language:
                    raise HTTPException(status_code=400, detail="Choose a template first.")
                template_row = (
                    await db.execute(
                        select(WhatsAppTemplate).where(
                            WhatsAppTemplate.user_id == user_id,
                            WhatsAppTemplate.name == data.template_name,
                            WhatsAppTemplate.language == data.template_language,
                        )
                    )
                ).scalar_one_or_none()
                params = WhatsAppController._resolve_params(
                    [p.model_dump() for p in (data.params or [])],
                    (user.full_name if user else "there"),
                )
                wamid = await whatsapp_service.send_template(
                    account.phone_number_id,
                    token,
                    phone,
                    data.template_name,
                    data.template_language,
                    params=params,
                    param_names=(template_row.body_param_names if template_row else None),
                )
            else:
                contact = (
                    await db.execute(
                        select(WhatsAppContact).where(
                            WhatsAppContact.user_id == user_id,
                            WhatsAppContact.phone_e164 == phone,
                        )
                    )
                ).scalar_one_or_none()
                if contact and contact.in_service_window():
                    wamid = await whatsapp_service.send_text(
                        account.phone_number_id, token, phone, data.text_body or "Test message"
                    )
                else:
                    wamid = await whatsapp_service.send_template(
                        account.phone_number_id,
                        token,
                        phone,
                        account.opener_template_name,
                        account.opener_template_language,
                        params=[(user.full_name if user else "there"), business_name],
                    )
            return {"status": "sent", "wa_message_id": wamid}
        except WhatsAppError as e:
            if e.auth:
                await WhatsAppController.mark_account_error(
                    db, account, "WhatsApp connection expired. Please reconnect."
                )
            raise HTTPException(status_code=502, detail=f"WhatsApp could not send this: {e}")

    # ------------------------------------------------------------------
    # webhook
    # ------------------------------------------------------------------

    @staticmethod
    async def handle_webhook(db: AsyncSession, payload: dict) -> None:
        if (payload or {}).get("object") != "whatsapp_business_account":
            return
        for entry in payload.get("entry") or []:
            waba_id = str(entry.get("id") or "")
            for change in entry.get("changes") or []:
                field = change.get("field")
                value = change.get("value") or {}
                account = await WhatsAppController._account_for_change(db, waba_id, value)
                if not account:
                    continue
                try:
                    if field == "messages":
                        await WhatsAppController._handle_statuses(db, account, value)
                        await WhatsAppController._handle_inbound(db, account, value)
                    elif field == "message_template_status_update":
                        await WhatsAppController._handle_template_update(db, account, value)
                    elif field in ("phone_number_quality_update", "account_update"):
                        await WhatsAppController._handle_account_update(db, account, field, value)
                except Exception as e:
                    print(f"WA WEBHOOK handler error ({field}): {type(e).__name__}: {e}")
                    await db.rollback()

    @staticmethod
    async def _account_for_change(
        db: AsyncSession, waba_id: str, value: dict
    ) -> Optional[WhatsAppAccount]:
        # phone_number_id identifies the account most precisely, and neither
        # column is unique, so never use scalar_one_or_none here.
        phone_number_id = (value.get("metadata") or {}).get("phone_number_id")
        if phone_number_id:
            account = (
                await db.execute(
                    select(WhatsAppAccount).where(
                        WhatsAppAccount.phone_number_id == str(phone_number_id)
                    )
                )
            ).scalars().first()
            if account:
                return account
        if waba_id:
            return (
                await db.execute(
                    select(WhatsAppAccount).where(WhatsAppAccount.waba_id == waba_id)
                )
            ).scalars().first()
        return None

    @staticmethod
    def _ts(raw) -> datetime:
        try:
            return datetime.utcfromtimestamp(int(raw))
        except (TypeError, ValueError):
            return datetime.utcnow()

    @staticmethod
    async def _handle_statuses(db: AsyncSession, account: WhatsAppAccount, value: dict) -> None:
        statuses = value.get("statuses") or []
        if not statuses:
            return
        changed = False
        for st in statuses:
            wamid = st.get("id")
            new_status = (st.get("status") or "").lower()
            if not wamid or not new_status:
                continue
            message = (
                await db.execute(
                    select(WhatsAppMessage).where(WhatsAppMessage.wa_message_id == wamid)
                )
            ).scalar_one_or_none()
            if not message or message.user_id != account.user_id:
                continue

            ts = WhatsAppController._ts(st.get("timestamp"))
            if new_status == "failed":
                errors = st.get("errors") or []
                err = errors[0] if errors else {}
                message.status = MSG_FAILED
                try:
                    message.error_code = int(err.get("code")) if err.get("code") else None
                except (TypeError, ValueError):
                    message.error_code = None
                message.error_message = (
                    (err.get("error_data") or {}).get("details")
                    or err.get("title")
                    or err.get("message")
                    or "WhatsApp rejected this message"
                )[:500]
                message.failed_at = ts
                changed = True

                from app.services.whatsapp_service import ACCOUNT_BLOCKED_CODES
                if message.error_code in ACCOUNT_BLOCKED_CODES:
                    account.status = ACCOUNT_ERROR
                    account.last_error = (
                        f"WhatsApp has restricted this account: {message.error_message}. "
                        "This usually means business verification is incomplete."
                    )[:500]
                    account.updated_at = datetime.utcnow()
                continue

            if new_status == "delivered":
                message.delivered_at = message.delivered_at or ts
            elif new_status == "read":
                message.read_at = message.read_at or ts
            elif new_status == "sent":
                message.sent_at = message.sent_at or ts

            # An opener waiting for (or having received) a reply keeps its own
            # lifecycle status; only the timestamps above are recorded.
            if message.status in (MSG_AWAITING_REPLY, MSG_REPLIED):
                changed = True
                continue
            current_rank = STATUS_RANK.get(message.status, -1)
            new_rank = STATUS_RANK.get(new_status, -1)
            if new_rank > current_rank:
                message.status = new_status
                message.updated_at = datetime.utcnow()
            changed = True

        if changed:
            await db.commit()

    @staticmethod
    async def _handle_inbound(db: AsyncSession, account: WhatsAppAccount, value: dict) -> None:
        messages = value.get("messages") or []
        if not messages:
            return
        user_id = account.user_id
        for msg in messages:
            raw_from = msg.get("from")
            phone = normalize_phone(raw_from)
            if not phone:
                continue
            ts = WhatsAppController._ts(msg.get("timestamp"))
            body = ((msg.get("text") or {}).get("body") or "") if msg.get("type") == "text" else ""

            contact = await WhatsAppController._upsert_contact(
                db, user_id, phone, inbound_at=ts
            )
            # Outbound sends already record the customer link, so the lookup
            # below only runs the first time a number messages in.
            if not contact.customer_id:
                found = await WhatsAppController._match_customer(db, user_id, phone)
                if found:
                    contact.customer_id = found.id
            matched = None
            if contact.customer_id:
                matched = (
                    await db.execute(
                        select(Customer).where(Customer.id == contact.customer_id)
                    )
                ).scalar_one_or_none()

            if body and STOP_WORDS.match(body):
                contact.opted_out = True
                contact.opted_out_at = ts
                contact.opt_out_source = OPT_OUT_INBOUND
                # An opt-out must not trigger the parked message.
                await db.execute(
                    update(WhatsAppMessage)
                    .where(
                        WhatsAppMessage.user_id == user_id,
                        WhatsAppMessage.to_phone == phone,
                        WhatsAppMessage.status == MSG_AWAITING_REPLY,
                    )
                    .values(status=MSG_CANCELLED, updated_at=datetime.utcnow())
                )
                await db.commit()
                continue

            if body and START_WORDS.match(body):
                contact.opted_out = False
                contact.opted_out_at = None
                contact.opt_out_source = None

            if matched:
                matched.last_contact = ts

            await db.commit()
            # The window is now open: deliver anything parked for this number.
            await WhatsAppController._release_parked_messages(db, user_id, phone)

    @staticmethod
    async def _release_parked_messages(db: AsyncSession, user_id: int, phone: str) -> int:
        """Queue the real text for every opener this number has replied to."""
        now = datetime.utcnow()
        parked = (
            await db.execute(
                select(WhatsAppMessage).where(
                    WhatsAppMessage.user_id == user_id,
                    WhatsAppMessage.to_phone == phone,
                    WhatsAppMessage.status == MSG_AWAITING_REPLY,
                )
            )
        ).scalars().all()

        released = 0
        for opener in parked:
            if opener.expires_at and opener.expires_at < now:
                opener.status = MSG_EXPIRED
                opener.updated_at = now
                continue
            if not opener.body:
                opener.status = MSG_REPLIED
                opener.updated_at = now
                continue
            follow_up = WhatsAppMessage(
                campaign_id=opener.campaign_id,
                user_id=user_id,
                customer_id=opener.customer_id,
                customer_name=opener.customer_name,
                to_phone=phone,
                kind=MSG_TEXT,
                body=opener.body,
                status=MSG_QUEUED,
                next_attempt_at=now,
                created_at=now,
                updated_at=now,
            )
            db.add(follow_up)
            await db.flush()
            opener.status = MSG_REPLIED
            opener.follow_up_message_id = follow_up.id
            opener.updated_at = now
            released += 1

        if parked:
            await db.commit()
        return released

    @staticmethod
    async def _handle_template_update(
        db: AsyncSession, account: WhatsAppAccount, value: dict
    ) -> None:
        name = value.get("message_template_name")
        language = value.get("message_template_language")
        event = value.get("event") or value.get("new_status")
        if not name or not event:
            return
        event = str(event).upper()

        row = (
            await db.execute(
                select(WhatsAppTemplate).where(
                    WhatsAppTemplate.user_id == account.user_id,
                    WhatsAppTemplate.name == name,
                    WhatsAppTemplate.language == (language or WhatsAppTemplate.language),
                )
            )
        ).scalars().first()
        if row:
            row.status = event
            row.synced_at = datetime.utcnow()
        else:
            # Unknown locally: force a refresh next time templates are listed.
            account.templates_synced_at = None

        if name == account.opener_template_name:
            account.opener_template_status = event
        account.updated_at = datetime.utcnow()
        await db.commit()

    @staticmethod
    async def _handle_account_update(
        db: AsyncSession, account: WhatsAppAccount, field: str, value: dict
    ) -> None:
        if field == "phone_number_quality_update":
            rating = value.get("current_limit") or value.get("quality_score")
            if isinstance(rating, dict):
                rating = rating.get("score")
            if rating:
                account.quality_rating = str(rating)[:16]
        else:
            event = value.get("event")
            if event:
                account.last_error = f"WhatsApp account update: {event}"[:500]
        account.updated_at = datetime.utcnow()
        await db.commit()
