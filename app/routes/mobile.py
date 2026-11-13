import os

from fastapi import Depends, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user_id
from app.models.customer import Customer
from app.models.order import Order
from slowapi.util import get_remote_address

from app.rate_limit import RateLimitedRouter
from app.services.auth_service import TokenService
from app.services.order_visibility import get_visibility

def _mobile_key(request: Request) -> str:
    """Count signed-in requests per user. Phones on mobile networks often
    share one IP address, so the IP is only used for unsigned requests."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        data = TokenService.get_token_data(auth[7:].strip())
        if data and data.get("sub") and data.get("type") != "refresh":
            return "user:" + str(data["sub"])
    return get_remote_address(request)


router = RateLimitedRouter(prefix="/api/mobile", tags=["mobile"], limit="120/minute", key_func=_mobile_key)

API_VERSION = 1


@router.get("/ping")
async def mobile_ping(request: Request):
    flag = os.getenv("ORDER_LINK_ENABLED", "false").strip().lower() in ("1", "true", "yes")
    return {"ok": True, "api_version": API_VERSION, "order_link_enabled": flag}


@router.get("/summary")
async def mobile_summary(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    hidden_ids, _limit, _over = await get_visibility(db, user_id)

    oq = select(Order.status, func.count(Order.id)).where(Order.user_id == user_id)
    if hidden_ids:
        oq = oq.where(Order.customer_id.notin_(hidden_ids))
    oq = oq.group_by(Order.status)
    by_status = {s: n for s, n in (await db.execute(oq)).all()}

    tq = select(func.count(Order.id)).where(
        Order.user_id == user_id,
        Order.tracking_enabled.is_(True),
        Order.status != "done",
    )
    if hidden_ids:
        tq = tq.where(Order.customer_id.notin_(hidden_ids))
    tracking_open = (await db.execute(tq)).scalar_one()

    cq = select(func.count(Customer.id)).where(Customer.user_id == user_id)
    customers = (await db.execute(cq)).scalar_one()

    return {
        "api_version": API_VERSION,
        "orders": {
            "pending": by_status.get("pending", 0),
            "paid": by_status.get("paid", 0),
            "on_the_way": by_status.get("on_the_way", 0),
            "done": by_status.get("done", 0),
            "tracking_open": tracking_open,
        },
        "customers": {"total": customers},
    }
