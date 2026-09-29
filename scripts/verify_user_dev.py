#!/usr/bin/env python
"""Local development helper: list accounts and verify one without email.

Signup emails often do not arrive in local development, which blocks login.
This reads the verification token straight from the database and either prints
the link or marks the account verified.

    python scripts/verify_user_dev.py                      # list accounts
    python scripts/verify_user_dev.py --email you@x.com    # print the link
    python scripts/verify_user_dev.py --email you@x.com --verify   # just verify
    python scripts/verify_user_dev.py --email you@x.com --set-password 'Passw0rd!'

Development only. It talks to whatever DATABASE_URL points at, so check that it
is your local database before using --verify.
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SECRET_KEY", "dev")

from sqlalchemy import select
from app.database import AsyncSessionLocal, DATABASE_URL
from app.models.user import User


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email")
    parser.add_argument("--verify", action="store_true", help="mark the account verified")
    parser.add_argument("--set-password", help="set a login password directly (also verifies)")
    parser.add_argument("--base-url", default="http://localhost:8001")
    args = parser.parse_args()

    if "sqlite" not in DATABASE_URL and args.verify:
        print(f"Refusing --verify: DATABASE_URL is not sqlite ({DATABASE_URL.split('://')[0]}).")
        print("Point DATABASE_URL at your local database first.")
        return 1

    async with AsyncSessionLocal() as db:
        if not args.email:
            rows = (await db.execute(select(User).order_by(User.id))).scalars().all()
            if not rows:
                print("No accounts yet. Sign up at /signup first.")
                return 0
            print(f"{'id':<5}{'email':<34}{'verified':<10}password set")
            for u in rows:
                print(f"{u.id:<5}{u.email:<34}{str(u.is_email_verified):<10}{bool(u.hashed_password)}")
            print("\nRerun with --email <address> to get that account's verification link.")
            return 0

        user = (
            await db.execute(select(User).where(User.email == args.email))
        ).scalar_one_or_none()
        if not user:
            print(f"No account with email {args.email}")
            return 1

        if args.set_password:
            from app.services.auth_service import PasswordService
            if not PasswordService.validate_password_strength(args.set_password):
                print("Password needs 8+ characters with upper case, lower case and a number.")
                return 1
            user.hashed_password = PasswordService.hash_password(args.set_password)
            user.is_email_verified = True
            user.email_verification_token = None
            await db.commit()
            print(f"{user.email} can now log in at {args.base_url}/login")
            return 0

        if user.is_email_verified and user.hashed_password:
            print(f"{user.email} is verified and has a password. You can log in.")
            return 0

        if user.is_email_verified and not user.hashed_password:
            print(f"{user.email} is verified but has no password yet.")
            if user.email_verification_token:
                print("\nFinish at:\n")
                print(f"  {args.base_url}/create-password?token={user.email_verification_token}")
            else:
                print("\nIts token was already cleared, so the create-password page cannot be used.")
                print("Set one directly instead:\n")
                print(f"  python scripts/verify_user_dev.py --email {user.email} --set-password 'YourPass1'")
            return 0

        if args.verify:
            user.is_email_verified = True
            # Keep the token when no password exists: the create-password page
            # looks the account up by it, so clearing it would strand the user.
            if user.hashed_password:
                user.email_verification_token = None
            await db.commit()
            print(f"{user.email} is now verified.")
            if not user.hashed_password:
                if user.email_verification_token:
                    print(f"\nSet a password at:\n  {args.base_url}/create-password?token={user.email_verification_token}")
                else:
                    print(f"\nNo password yet. Set one with:\n  python scripts/verify_user_dev.py --email {user.email} --set-password 'YourPass1'")
            else:
                print(f"Log in at {args.base_url}/login")
            return 0

        if not user.email_verification_token:
            print(f"{user.email} is unverified but has no token. Rerun with --verify.")
            return 1

        print("Open this link to verify:\n")
        print(f"  {args.base_url}/verify-email?token={user.email_verification_token}")
        print("\nOr skip the link entirely with --verify.")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
