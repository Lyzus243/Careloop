from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.models.order import Order
from app.plans import PLANS
from app.services.billing_service import current_plan


async def get_over_limit_customer_ids(db: AsyncSession, user_id: int, limit: int | None):
    """Return the customer ids that sit past the plan limit. The first `limit`
    customers (by created_at) stay visible; anyone created after is hidden.
    Upgrading the plan moves customers back into view."""
    if limit is None:
        return set()
    result = await db.execute(
        select(Customer.id)
        .where(Customer.user_id == user_id)
        .order_by(Customer.created_at.asc())
        .offset(limit)
    )
    return {r[0] for r in result.all()}


async def get_visibility(db: AsyncSession, user_id: int):
    """Returns (hidden_customer_ids, limit, over_limit)."""
    plan = await current_plan(db, user_id)
    limit = PLANS[plan]["customer_limit"]
    hidden_ids = await get_over_limit_customer_ids(db, user_id, limit)
    return hidden_ids, limit, bool(hidden_ids)
