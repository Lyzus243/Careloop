from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user_id
from app.models.order import Order, OrderItem
from app.rate_limit import RateLimitedRouter
from app.services.storage import presigned_download_url, receipt_path

router = RateLimitedRouter(prefix="/api/orders", tags=["orders"], limit="60/minute")


@router.get("")
async def list_orders(
    request: Request,
    status: str | None = None,
    after_id: int = 0,
    limit: int = 20,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Cursor-paginated list, oldest first. after_id=0 starts from the beginning."""
    limit = max(1, min(limit, 100))

    q = select(Order).where(Order.user_id == user_id, Order.id > after_id)
    if status:
        q = q.where(Order.status == status)
    q = q.order_by(Order.id.asc()).limit(limit + 1)

    result = await db.execute(q)
    rows = list(result.scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]

    counts = {}
    if rows:
        ids = [o.id for o in rows]
        cq = await db.execute(
            select(OrderItem.order_id, func.count(OrderItem.id))
            .where(OrderItem.order_id.in_(ids))
            .group_by(OrderItem.order_id)
        )
        counts = dict(cq.all())

    return {
        "orders": [
            {
                "id": o.id,
                "customer_name": o.customer_name,
                "customer_phone": o.customer_phone,
                "status": o.status,
                "total": float(o.total),
                "delivery_type": o.delivery_type,
                "created_at": o.created_at.isoformat() if o.created_at else None,
                "item_count": counts.get(o.id, 0),
            }
            for o in rows
        ],
        "next_cursor": rows[-1].id if rows else None,
        "has_more": has_more,
    }


@router.get("/pending-count")
async def pending_order_count(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(func.count(Order.id)).where(
            Order.user_id == user_id, Order.status == "pending"
        )
    )
    return {"count": result.scalar_one()}


@router.get("/{order_id}")
async def get_order_detail(
    order_id: int,
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Order).where(Order.id == order_id, Order.user_id == user_id)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    items_result = await db.execute(
        select(OrderItem).where(OrderItem.order_id == order_id).order_by(OrderItem.id.asc())
    )
    items = items_result.scalars().all()

    return {
        "id": order.id,
        "customer_name": order.customer_name,
        "customer_phone": order.customer_phone,
        "customer_birthday": order.customer_birthday,
        "status": order.status,
        "delivery_type": order.delivery_type,
        "delivery_address": order.delivery_address,
        "note_to_seller": order.note_to_seller,
        "subtotal": float(order.subtotal),
        "total": float(order.total),
        "has_receipt": bool(order.receipt_key),
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "items": [
            {
                "id": i.id,
                "product_name": i.product_name_snapshot,
                "unit_label": i.unit_label_snapshot,
                "unit_price": float(i.unit_price_snapshot),
                "quantity": i.quantity,
                "line_total": float(i.line_total),
            }
            for i in items
        ],
    }


_VALID_TRANSITIONS = {
    "pending": {"paid"},
    "paid": {"on_the_way"},
    "on_the_way": {"done"},
    "done": set(),
}


@router.patch("/{order_id}/status")
async def update_order_status(
    order_id: int,
    request: Request,
    data: dict,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    new_status = (data.get("status") or "").strip()
    if new_status not in ("pending", "paid", "on_the_way", "done"):
        raise HTTPException(status_code=400, detail="Invalid status")

    result = await db.execute(
        select(Order).where(Order.id == order_id, Order.user_id == user_id)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if new_status == order.status:
        return {"success": True, "status": order.status}

    allowed = _VALID_TRANSITIONS.get(order.status, set())
    if new_status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot move from {order.status} to {new_status}",
        )

    order.status = new_status

    # First completed order -> mark customer active + has_purchased
    if new_status == "done" and order.customer_id:
        from app.models.customer import Customer, CustomerType
        cust_res = await db.execute(select(Customer).where(Customer.id == order.customer_id))
        cust = cust_res.scalar_one_or_none()
        if cust:
            cust.has_purchased = True
            if cust.customer_type == CustomerType.NEW:
                cust.customer_type = CustomerType.ACTIVE

    await db.commit()
    return {"success": True, "status": order.status}


@router.get("/{order_id}/receipt")
async def get_receipt_url(
    order_id: int,
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Return either a presigned URL (S3) or the file itself (local disk).
    Only the owning business can fetch it."""
    result = await db.execute(
        select(Order).where(Order.id == order_id, Order.user_id == user_id)
    )
    order = result.scalar_one_or_none()
    if not order or not order.receipt_key:
        raise HTTPException(status_code=404, detail="Receipt not found")

    url = presigned_download_url(order.receipt_key)
    if url:
        return {"url": url, "expires_in": 900}

    p = receipt_path(order.receipt_key)
    if not p:
        raise HTTPException(status_code=404, detail="Receipt file missing")
    return FileResponse(p)
