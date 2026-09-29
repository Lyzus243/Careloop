"""Thin async client for the Meta WhatsApp Cloud API (Graph API).

Follows the same singleton pattern as email_service / openai_service: read config
from the environment at import time and disable the integration when it is
incomplete, rather than crashing the app.

Security notes:
  * Access tokens are always sent in the Authorization header, never as a query
    parameter, so they do not end up in intermediary logs.
  * The httpx logger is raised to WARNING because its INFO line prints full
    request URLs, and the OAuth exchange URL carries client_secret and code.
"""
import os
import hmac
import hashlib
import logging
import re
from typing import Optional, List, Dict, Any

from dotenv import load_dotenv

if os.path.exists(".env"):
    load_dotenv(dotenv_path=".env", override=False)

import httpx

from app.utils import crypto

logger = logging.getLogger(__name__)

# httpx logs full URLs at INFO, which would leak client_secret / code.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


# --- Graph error codes ------------------------------------------------------

# Transient: worth retrying with backoff.
RETRYABLE_CODES = {4, 80007, 130429, 131048, 131056, 131000, 133016}
# The 24-hour customer service window closed, or the recipient cannot receive.
WINDOW_CODES = {131047, 131026, 131051}
# The token is dead. Retrying will never help; the vendor must reconnect.
AUTH_CODES = {190, 10, 200, 2500}
# The template itself is wrong, so every message in the campaign will fail.
TEMPLATE_CODES = {132000, 132001, 132005, 132007, 132012, 132015, 132016, 132068, 132069}
# Recipient not in the dev allow-list (test numbers only).
NOT_ALLOWED_CODES = {131030}
# The whole account is blocked, not this one message. Every other send will fail
# the same way, so the vendor has to be told rather than shown N identical errors.
ACCOUNT_BLOCKED_CODES = {
    131031,  # Business Account locked
    131042,  # business eligibility / payment issue
    131045,  # phone number not registered
    368,     # temporarily blocked for policy violations
}

# The opener template Careloop creates on each vendor's WABA. It is what makes
# custom (free-form) bulk messaging possible: customers outside the 24-hour
# window get this first, and their reply opens the window for the real message.
OPENER_TEMPLATE_NAME = "careloop_message_opener"
OPENER_TEMPLATE_LANGUAGE = "en"
OPENER_TEMPLATE_CATEGORY = "MARKETING"
OPENER_TEMPLATE_BODY = "Hi {{1}}, {{2}} has a message for you. Reply YES to see it."
OPENER_TEMPLATE_EXAMPLES = ["Ada", "Bella's Boutique"]

MAX_PARAM_LENGTH = 1024
MAX_TEXT_LENGTH = 4096

_WHITESPACE_RUN = re.compile(r"[\r\n\t]+| {4,}")


class WhatsAppError(Exception):
    """A mapped Graph API failure."""

    def __init__(
        self,
        message: str,
        code: Optional[int] = None,
        subcode: Optional[int] = None,
        http_status: Optional[int] = None,
        retryable: bool = False,
        window: bool = False,
        auth: bool = False,
        template: bool = False,
        account_blocked: bool = False,
        details: Optional[str] = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.subcode = subcode
        self.http_status = http_status
        self.retryable = retryable
        self.window = window
        self.auth = auth
        self.template = template
        self.account_blocked = account_blocked
        self.details = details

    def __str__(self) -> str:
        parts = [self.message]
        if self.code is not None:
            parts.append(f"(code {self.code})")
        return " ".join(parts)


def sanitize_param(value: str) -> str:
    """Meta rejects template parameters containing newlines, tabs or 4+ spaces."""
    if value is None:
        return ""
    cleaned = _WHITESPACE_RUN.sub(" ", str(value)).strip()
    return cleaned[:MAX_PARAM_LENGTH]


class WhatsAppService:
    def __init__(self):
        self.app_id = os.getenv("META_APP_ID")
        self.app_secret = os.getenv("META_APP_SECRET")
        self.config_id = os.getenv("META_CONFIG_ID")
        self.graph_version = os.getenv("META_GRAPH_VERSION", "v21.0")
        self.webhook_verify_token = os.getenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN")
        self.phone_pin = os.getenv("WHATSAPP_PHONE_PIN", "000000")
        self.allow_manual_connect = (
            os.getenv("WHATSAPP_ALLOW_MANUAL_CONNECT", "false").strip().lower()
            in ("1", "true", "yes")
        )
        self.base_url = f"https://graph.facebook.com/{self.graph_version}"
        self._client: Optional[httpx.AsyncClient] = None

        # Sending messages and verifying webhooks need only the app credentials.
        self.enabled = bool(self.app_id and self.app_secret and crypto.is_available())
        # Embedded Signup additionally needs the configuration id, which Meta only
        # issues after Tech Provider approval. Everything else works without it.
        self.signup_enabled = bool(self.enabled and self.config_id)

        if not self.enabled:
            missing = [
                n for n, v in (
                    ("META_APP_ID", self.app_id),
                    ("META_APP_SECRET", self.app_secret),
                    ("WHATSAPP_TOKEN_ENCRYPTION_KEY", crypto.is_available() or None),
                ) if not v
            ]
            print(f"WARNING: WhatsApp integration disabled, missing: {', '.join(missing)}")
        elif not self.signup_enabled:
            print(
                "WhatsApp Cloud API configured (sending and webhooks active). "
                "META_CONFIG_ID not set, so Embedded Signup is unavailable; use "
                "manual connect until Tech Provider approval comes through."
            )
        else:
            print("WhatsApp Cloud API configured successfully")

    # --- plumbing ----------------------------------------------------------

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(20.0, connect=10.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._client

    async def aclose(self):
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    def _map_error(self, payload: dict, http_status: int) -> WhatsAppError:
        err = (payload or {}).get("error", {}) or {}
        code = err.get("code")
        subcode = err.get("error_subcode")
        message = err.get("message") or f"WhatsApp API error (HTTP {http_status})"
        details = (err.get("error_data") or {}).get("details")
        try:
            code = int(code) if code is not None else None
        except (TypeError, ValueError):
            code = None

        retryable = (
            http_status == 429
            or http_status >= 500
            or (code in RETRYABLE_CODES)
        )
        return WhatsAppError(
            message=message,
            code=code,
            subcode=subcode,
            http_status=http_status,
            retryable=retryable,
            window=code in WINDOW_CODES,
            auth=code in AUTH_CODES,
            template=code in TEMPLATE_CODES,
            account_blocked=code in ACCOUNT_BLOCKED_CODES,
            details=details,
        )

    async def _request(
        self,
        method: str,
        path: str,
        token: Optional[str] = None,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> dict:
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        client = self._get_client()
        try:
            resp = await client.request(method, url, headers=headers, json=json, params=params)
        except httpx.TimeoutException as e:
            raise WhatsAppError(f"WhatsApp API timed out: {e}", retryable=True) from e
        except httpx.HTTPError as e:
            raise WhatsAppError(f"WhatsApp API unreachable: {e}", retryable=True) from e

        try:
            payload = resp.json()
        except Exception:
            payload = {}

        if resp.status_code >= 400:
            raise self._map_error(payload, resp.status_code)
        return payload

    # --- onboarding --------------------------------------------------------

    async def exchange_code(self, code: str) -> str:
        """Swap the Embedded Signup code for a business integration system user token."""
        payload = await self._request(
            "GET",
            "oauth/access_token",
            params={
                "client_id": self.app_id,
                "client_secret": self.app_secret,
                "code": code,
            },
        )
        token = payload.get("access_token")
        if not token:
            raise WhatsAppError("Meta did not return an access token")
        return token

    async def debug_token(self, token: str) -> dict:
        """Inspect a token so we can bind it to the WABA the client claims."""
        payload = await self._request(
            "GET",
            "debug_token",
            params={
                "input_token": token,
                "access_token": f"{self.app_id}|{self.app_secret}",
            },
        )
        return payload.get("data", {}) or {}

    def token_issued_to_this_app(self, token_data: dict) -> bool:
        """True when the token was minted by this Meta app."""
        return str(token_data.get("app_id")) == str(self.app_id)

    def token_lists_waba(self, token_data: dict, waba_id: str) -> bool:
        """True when the token's granular scopes name this WABA explicitly.

        Note this returns False when a scope carries no target_ids, because an
        absent list says nothing about which assets were granted. Treating it as
        "all assets" would let a caller claim any WABA id. Use it only as a fast
        accept; verify_waba_access is the authoritative check.
        """
        if not self.token_issued_to_this_app(token_data):
            return False
        for scope in token_data.get("granular_scopes") or []:
            if scope.get("scope") in (
                "whatsapp_business_management",
                "whatsapp_business_messaging",
            ):
                target_ids = scope.get("target_ids") or []
                if str(waba_id) in [str(t) for t in target_ids]:
                    return True
        return False

    async def verify_waba_access(self, waba_id: str, token: str) -> bool:
        """Ask Meta whether this token can actually read this WABA.

        Graph returns the object only to a token that genuinely holds access, so
        this is authoritative regardless of how granular_scopes are populated.
        It is what stops a caller pairing their own token with someone else's
        WhatsApp Business Account id.
        """
        try:
            result = await self._request(
                "GET", str(waba_id), token=token, params={"fields": "id,name"}
            )
        except WhatsAppError:
            return False
        return str(result.get("id") or "") == str(waba_id)

    async def subscribe_app(self, waba_id: str, token: str) -> None:
        await self._request("POST", f"{waba_id}/subscribed_apps", token=token)

    async def unsubscribe_app(self, waba_id: str, token: str) -> None:
        await self._request("DELETE", f"{waba_id}/subscribed_apps", token=token)

    async def register_phone(self, phone_number_id: str, token: str, pin: Optional[str] = None) -> None:
        await self._request(
            "POST",
            f"{phone_number_id}/register",
            token=token,
            json={"messaging_product": "whatsapp", "pin": pin or self.phone_pin},
        )

    async def get_phone_number(self, phone_number_id: str, token: str) -> dict:
        return await self._request(
            "GET",
            phone_number_id,
            token=token,
            params={
                "fields": "display_phone_number,verified_name,quality_rating,code_verification_status,platform_type"
            },
        )

    async def list_phone_numbers(self, waba_id: str, token: str) -> List[dict]:
        payload = await self._request(
            "GET",
            f"{waba_id}/phone_numbers",
            token=token,
            params={"fields": "id,display_phone_number,verified_name,quality_rating"},
        )
        return payload.get("data", []) or []

    # --- templates ---------------------------------------------------------

    async def list_templates(self, waba_id: str, token: str) -> List[dict]:
        """Fetch every template on the WABA, following Graph pagination."""
        results: List[dict] = []
        path = f"{waba_id}/message_templates"
        params = {
            "fields": "id,name,language,status,category,parameter_format,components",
            "limit": 100,
        }
        pages = 0
        while path and pages < 20:
            payload = await self._request("GET", path, token=token, params=params)
            results.extend(payload.get("data", []) or [])
            path = (payload.get("paging") or {}).get("next")
            params = None  # the "next" URL already carries the query string
            pages += 1
        return results

    async def create_template(
        self,
        waba_id: str,
        token: str,
        name: str,
        language: str,
        category: str,
        components: List[dict],
    ) -> dict:
        """Submit a template for Meta review. Tolerates an existing duplicate."""
        try:
            return await self._request(
                "POST",
                f"{waba_id}/message_templates",
                token=token,
                json={
                    "name": name,
                    "language": language,
                    "category": category,
                    "components": components,
                },
            )
        except WhatsAppError as e:
            # 132001 here means "a template with this name/language already
            # exists", which is the desired end state on reconnect.
            if e.code in (132001,) or (e.message and "already exists" in e.message.lower()):
                return {"status": "EXISTS", "duplicate": True}
            raise

    async def create_opener_template(self, waba_id: str, token: str) -> dict:
        return await self.create_template(
            waba_id,
            token,
            name=OPENER_TEMPLATE_NAME,
            language=OPENER_TEMPLATE_LANGUAGE,
            category=OPENER_TEMPLATE_CATEGORY,
            components=[
                {
                    "type": "BODY",
                    "text": OPENER_TEMPLATE_BODY,
                    "example": {"body_text": [OPENER_TEMPLATE_EXAMPLES]},
                }
            ],
        )

    # --- sending -----------------------------------------------------------

    async def send_template(
        self,
        phone_number_id: str,
        token: str,
        to: str,
        name: str,
        language: str,
        params: Optional[List[str]] = None,
        param_names: Optional[List[str]] = None,
    ) -> str:
        """Send a template message. Returns the wamid used to match webhooks."""
        template: Dict[str, Any] = {"name": name, "language": {"code": language}}
        if params:
            parameters = []
            for idx, value in enumerate(params):
                param: Dict[str, Any] = {"type": "text", "text": sanitize_param(value)}
                if param_names and idx < len(param_names) and param_names[idx]:
                    param["parameter_name"] = param_names[idx]
                parameters.append(param)
            template["components"] = [{"type": "body", "parameters": parameters}]

        payload = await self._request(
            "POST",
            f"{phone_number_id}/messages",
            token=token,
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "template",
                "template": template,
            },
        )
        return self._extract_wamid(payload)

    async def send_text(self, phone_number_id: str, token: str, to: str, body: str) -> str:
        payload = await self._request(
            "POST",
            f"{phone_number_id}/messages",
            token=token,
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"preview_url": False, "body": (body or "")[:MAX_TEXT_LENGTH]},
            },
        )
        return self._extract_wamid(payload)

    @staticmethod
    def _extract_wamid(payload: dict) -> str:
        messages = payload.get("messages") or []
        if messages and messages[0].get("id"):
            return messages[0]["id"]
        raise WhatsAppError("WhatsApp accepted the request but returned no message id")

    # --- webhook security --------------------------------------------------

    def verify_webhook_signature(self, raw_body: bytes, header: Optional[str]) -> bool:
        """Validate X-Hub-Signature-256 over the raw request body."""
        if not self.app_secret or not header:
            return False
        if not header.startswith("sha256="):
            return False
        expected = hmac.new(
            self.app_secret.encode("utf-8"), raw_body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, header[len("sha256="):])

    def verify_token_matches(self, provided: Optional[str]) -> bool:
        if not self.webhook_verify_token or not provided:
            return False
        return hmac.compare_digest(self.webhook_verify_token, provided)


whatsapp_service = WhatsAppService()
