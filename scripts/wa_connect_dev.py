#!/usr/bin/env python
"""Connect a Careloop account to the Meta WhatsApp test number, for development.

Logs in with your Careloop credentials, then calls /api/whatsapp/connect/manual
so you can test without waiting for Tech Provider approval and Embedded Signup.

Requires WHATSAPP_ALLOW_MANUAL_CONNECT=true in .env (already set).

Usage:
    python scripts/wa_connect_dev.py --token "EAAxxxxx..."

The Meta test token expires after 24 hours; rerun this with a fresh one.
"""
import argparse
import asyncio
import getpass
import sys

import httpx

# Identifiers from the Meta "Try it out" panel. Not secrets, unlike the token.
DEFAULT_PHONE_NUMBER_ID = "1397153286808572"
DEFAULT_WABA_ID = "4146427932314056"
DEFAULT_BASE_URL = "http://localhost:8001"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token", required=True, help="Meta temporary access token")
    parser.add_argument("--phone-number-id", default=DEFAULT_PHONE_NUMBER_ID)
    parser.add_argument("--waba-id", default=DEFAULT_WABA_ID)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--email", help="Careloop login email")
    args = parser.parse_args()

    email = args.email or input("Careloop email: ").strip()
    password = getpass.getpass("Careloop password: ")

    async with httpx.AsyncClient(base_url=args.base_url, timeout=60) as client:
        print(f"\nLogging in to {args.base_url} ...")
        r = await client.post("/api/auth/login", json={"email": email, "password": password})
        if r.status_code != 200:
            print(f"  Login failed ({r.status_code}): {r.text[:300]}")
            return 1
        jwt = r.json().get("access_token")
        if not jwt:
            print(f"  No access_token in login response: {r.text[:300]}")
            return 1
        print("  Logged in.")

        headers = {"Authorization": f"Bearer {jwt}"}

        print("Connecting WhatsApp test number ...")
        r = await client.post(
            "/api/whatsapp/connect/manual",
            headers=headers,
            json={
                "access_token": args.token,
                "waba_id": args.waba_id,
                "phone_number_id": args.phone_number_id,
            },
        )
        if r.status_code != 200:
            print(f"  Connect failed ({r.status_code}): {r.text[:500]}")
            if r.status_code == 404:
                print("  Hint: set WHATSAPP_ALLOW_MANUAL_CONNECT=true and restart the server.")
            return 1

        status = r.json()
        print("  Connected.")
        print(f"    number:  {status.get('display_phone_number')}")
        print(f"    name:    {status.get('verified_name')}")
        print(f"    quality: {status.get('quality_rating')}")
        print(f"    intro template: {status.get('opener_template_status')}")
        if status.get("last_error"):
            print(f"    warnings: {status['last_error']}")

        print("\nFetching templates ...")
        r = await client.get("/api/whatsapp/templates?refresh=true", headers=headers)
        if r.status_code == 200:
            for t in r.json():
                print(f"    {t['name']} ({t['language']}) - {t['status']}, {t['body_param_count']} variable(s)")
        else:
            print(f"    Could not load templates ({r.status_code}): {r.text[:300]}")

    print("\nDone. Open the dashboard, go to Account Settings, and the WhatsApp card")
    print("should show the test number as connected.")
    print("\nReminder: Meta only delivers to numbers on your test allow list.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
