"""Cancel every paid Careloop plan while subscriptions are switched off.

For each user on a paid plan, this stops their Paystack subscription (and any
scheduled downgrade) so they aren't charged again, then moves them to the Free
plan. Users get no email, and nobody is refunded: issue refunds from the
Paystack dashboard if you want to.

    python cancel_paid_subscriptions.py          # list who would be cancelled
    python cancel_paid_subscriptions.py --yes    # cancel them

Reads DATABASE_URL and PAYSTACK_SECRET_KEY from the environment or .env, so run it
with the production values to cancel real subscriptions. Safe to run again: anyone
whose cancellation failed is still listed next time.
"""
import argparse
import asyncio
import sys

import app.models  # noqa: F401  registers every table
from app.database import AsyncSessionLocal
from app.models.user import User
from app.plans import PLANS, subscriptions_enabled
from app.services.billing_service import paid_subscriptions, cancel_to_free
from app.services.paystack_service import paystack_service


def describe(sub, user) -> str:
    email = user.email if user else f"(deleted user {sub.user_id})"
    renews = sub.current_period_end.strftime("%d %b %Y") if sub.current_period_end else "-"
    codes = ", ".join(c for c in (sub.paystack_subscription_code, sub.pending_subscription_code) if c) or "none"
    return f"  {email:<40} {PLANS[sub.plan]['name']:<11} {sub.status:<9} period ends {renews:<12} Paystack: {codes}"


async def main(apply: bool) -> int:
    if subscriptions_enabled():
        print("Subscriptions are switched on (SUBSCRIPTIONS_ENABLED in app/plans.py). "
              "This script is only for while they're off, so nothing was changed.")
        return 1
    if apply and not paystack_service.configured:
        print("PAYSTACK_SECRET_KEY isn't set, so Paystack subscriptions can't be cancelled. Nothing was changed.")
        return 1

    async with AsyncSessionLocal() as db:
        subs = await paid_subscriptions(db)
        if not subs:
            print("No paid subscriptions. Nothing to do.")
            return 0

        users = {sub.id: await db.get(User, sub.user_id) for sub in subs}
        print(f"{len(subs)} paid subscription(s):")
        for sub in subs:
            print(describe(sub, users[sub.id]))

        if not apply:
            print("\nDry run: nothing was changed. Run again with --yes to cancel these and move them to Free.")
            return 0

        print()
        failed = 0
        for sub in subs:
            user = users[sub.id]
            email = user.email if user else f"user {sub.user_id}"
            errors = await cancel_to_free(db, sub)
            if errors:
                failed += 1
                print(f"  FAILED    {email}: {'; '.join(errors)}")
            else:
                print(f"  cancelled {email}")

        print(f"\n{len(subs) - failed} cancelled, {failed} failed.")
        if failed:
            print("Failed users were left unchanged. Check them in the Paystack dashboard, then run this again.")
        return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cancel every paid plan while subscriptions are switched off.")
    parser.add_argument("--yes", action="store_true", help="actually cancel (without it, only lists)")
    sys.exit(asyncio.run(main(parser.parse_args().yes)))
