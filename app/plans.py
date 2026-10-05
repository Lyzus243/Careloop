import os
from typing import Optional

# Subscription plans, cheapest first. Prices are in naira and only drive what the
# dashboard shows: Paystack charges whatever amount is set on the plan in the
# Paystack dashboard, so keep the two in step.
# A customer_limit of None means unlimited.
# A plan with no price, or no Paystack plan code in the environment, can't be bought yet.
# SAMPLE PRICES: replace with Careloop's real prices before launch.
PLANS = {
    "free": {
        "name": "Free",
        "customer_limit": 10,
        "price": 0,
        "original_price": None,
        "plan_code_env": None,
        "bulk_messaging": False,
    },
    "basic": {
        "name": "Basic",
        "customer_limit": 150,
        "price": 1499,
        "original_price": 4499,
        "plan_code_env": "PAYSTACK_PLAN_BASIC",
        "bulk_messaging": False,
    },
    "standard": {
        "name": "Standard",
        "customer_limit": 250,
        "price": 3499,
        "original_price": 6499,
        "plan_code_env": "PAYSTACK_PLAN_STANDARD",
        "bulk_messaging": False,
    },
    "premium": {
        "name": "Premium",
        "customer_limit": None,
        "price": 7499,
        "original_price": 10499,
        "plan_code_env": "PAYSTACK_PLAN_PREMIUM",
        "bulk_messaging": False,
    },
    "automation": {
        "name": "Automation",
        "customer_limit": None,
        "price": None,  # set once the Automation price is agreed
        "original_price": None,
        "plan_code_env": "PAYSTACK_PLAN_AUTOMATION",
        "bulk_messaging": True,
    },
}

FREE_PLAN = "free"

# Days a user keeps their paid plan after a failed renewal before dropping to Free limits.
PAYMENT_GRACE_DAYS = 3


def plan_rank(plan: str) -> int:
    return list(PLANS).index(plan)


def paystack_plan_code(plan: str) -> Optional[str]:
    env = PLANS[plan]["plan_code_env"]
    return os.getenv(env) if env else None


def plan_for_paystack_code(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    for key in PLANS:
        if paystack_plan_code(key) == code:
            return key
    return None


def is_purchasable(plan: str) -> bool:
    return plan != FREE_PLAN and PLANS[plan]["price"] is not None and bool(paystack_plan_code(plan))
