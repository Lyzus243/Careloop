"""Thin async client for the Paystack endpoints Careloop's subscriptions use."""
import hashlib
import hmac
import os
from datetime import datetime
from typing import Any, Optional

import httpx

PAYSTACK_BASE_URL = "https://api.paystack.co"


class PaystackError(Exception):
    pass


class PaystackService:
    def __init__(self):
        self.secret_key = os.getenv("PAYSTACK_SECRET_KEY")
        if not self.secret_key:
            print("WARNING: PAYSTACK_SECRET_KEY not found - payments are disabled")

    @property
    def configured(self) -> bool:
        return bool(self.secret_key)

    async def _request(self, method: str, path: str, json: Optional[dict] = None) -> Any:
        if not self.secret_key:
            raise PaystackError("Payments are not set up yet.")
        try:
            async with httpx.AsyncClient(base_url=PAYSTACK_BASE_URL, timeout=20) as client:
                r = await client.request(
                    method, path, json=json,
                    headers={"Authorization": f"Bearer {self.secret_key}"},
                )
        except httpx.HTTPError as e:
            raise PaystackError(f"Could not reach Paystack: {e}") from e
        body = r.json() if r.content else {}
        if r.status_code >= 400 or not body.get("status"):
            raise PaystackError(body.get("message") or f"Paystack returned status {r.status_code}")
        return body.get("data")

    def verify_signature(self, raw_body: bytes, signature: Optional[str]) -> bool:
        if not self.secret_key or not signature:
            return False
        expected = hmac.new(self.secret_key.encode(), raw_body, hashlib.sha512).hexdigest()
        return hmac.compare_digest(expected, signature)

    async def initialize_transaction(
        self, email: str, plan_code: str, amount_kobo: int, callback_url: str, metadata: dict
    ) -> dict:
        # With a plan code Paystack charges the plan's amount and creates the subscription itself.
        return await self._request("POST", "/transaction/initialize", {
            "email": email,
            "amount": amount_kobo,
            "plan": plan_code,
            "callback_url": callback_url,
            "metadata": metadata,
        })

    async def create_subscription(
        self, customer_code: str, plan_code: str, authorization_code: str, start_date: datetime
    ) -> dict:
        return await self._request("POST", "/subscription", {
            "customer": customer_code,
            "plan": plan_code,
            "authorization": authorization_code,
            "start_date": start_date.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        })

    async def fetch_subscription(self, code: str) -> dict:
        return await self._request("GET", f"/subscription/{code}")

    async def disable_subscription(self, code: str, email_token: Optional[str] = None) -> None:
        if not email_token:
            email_token = (await self.fetch_subscription(code)).get("email_token")
        await self._request("POST", "/subscription/disable", {"code": code, "token": email_token})

    async def enable_subscription(self, code: str, email_token: Optional[str] = None) -> None:
        if not email_token:
            email_token = (await self.fetch_subscription(code)).get("email_token")
        await self._request("POST", "/subscription/enable", {"code": code, "token": email_token})

    async def manage_link(self, code: str) -> str:
        """A Paystack page where the subscriber can update the card paying for a subscription."""
        data = await self._request("GET", f"/subscription/{code}/manage/link")
        return data["link"]


paystack_service = PaystackService()
