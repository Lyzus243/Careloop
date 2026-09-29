"""Ownership checks: a caller must not be able to attach someone else's
WhatsApp Business Account to their Careloop account."""
import pytest
import respx
import httpx

from app.services.whatsapp_service import whatsapp_service as w

OURS = "4146427932314056"
THEIRS = "9999999999999999"


def test_token_from_another_app_is_rejected():
    assert w.token_issued_to_this_app({"app_id": "999"}) is False
    assert w.token_issued_to_this_app({"app_id": w.app_id}) is True


def test_scope_without_target_ids_does_not_grant_everything():
    """The regression: an absent target_ids list says nothing about access.

    Treating it as 'all assets' let any caller claim any WABA id.
    """
    token_data = {
        "app_id": w.app_id,
        "granular_scopes": [
            {"scope": "whatsapp_business_management", "target_ids": None},
            {"scope": "whatsapp_business_messaging"},
        ],
    }
    assert w.token_lists_waba(token_data, OURS) is False
    assert w.token_lists_waba(token_data, THEIRS) is False


def test_scope_with_explicit_target_ids_grants_only_those():
    token_data = {
        "app_id": w.app_id,
        "granular_scopes": [
            {"scope": "whatsapp_business_management", "target_ids": [OURS]},
        ],
    }
    assert w.token_lists_waba(token_data, OURS) is True
    assert w.token_lists_waba(token_data, THEIRS) is False


@respx.mock
async def test_verify_waba_access_accepts_owned_account():
    respx.get(f"{w.base_url}/{OURS}").mock(
        return_value=httpx.Response(200, json={"id": OURS, "name": "Bella's Boutique"})
    )
    assert await w.verify_waba_access(OURS, "tok") is True


@respx.mock
async def test_verify_waba_access_rejects_unowned_account():
    respx.get(f"{w.base_url}/{THEIRS}").mock(
        return_value=httpx.Response(
            403, json={"error": {"code": 200, "message": "Permissions error"}}
        )
    )
    assert await w.verify_waba_access(THEIRS, "tok") is False


@respx.mock
async def test_verify_waba_access_rejects_mismatched_id():
    """Meta returning a different object must not count as access."""
    respx.get(f"{w.base_url}/{THEIRS}").mock(
        return_value=httpx.Response(200, json={"id": OURS})
    )
    assert await w.verify_waba_access(THEIRS, "tok") is False


@respx.mock
async def test_manual_connect_refuses_unowned_waba(client, db, user):
    """The dev-only path must not bypass the ownership check either."""
    respx.get(f"{w.base_url}/{THEIRS}").mock(
        return_value=httpx.Response(403, json={"error": {"code": 200, "message": "no"}})
    )
    r = await client.post("/api/whatsapp/connect/manual", json={
        "access_token": "someone-elses-valid-token",
        "waba_id": THEIRS,
        "phone_number_id": "123",
    })
    assert r.status_code == 403
    assert "does not have access" in r.json()["detail"]
