from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user_id
from app.models.order import Order, OrderItem
from app.rate_limit import RateLimitedRouter
from app.services.storage import presigned_download_url, receipt_path
from app.services.order_visibility import get_visibility

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

    hidden_ids, _limit, _over = await get_visibility(db, user_id)

    # Count hidden orders for the current tab so the UI can show a banner
    hidden_count = 0
    if hidden_ids:
        hq = select(func.count(Order.id)).where(
            Order.user_id == user_id,
            Order.customer_id.in_(hidden_ids),
        )
        if status:
            hq = hq.where(Order.status == status)
        hidden_count = (await db.execute(hq)).scalar_one()

    q = select(Order).where(Order.user_id == user_id, Order.id > after_id)
    if hidden_ids:
        q = q.where(Order.customer_id.notin_(hidden_ids))
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
        "hidden_count": hidden_count,
    }


@router.get("/pending-count")
async def pending_order_count(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    hidden_ids, _limit, _over = await get_visibility(db, user_id)
    q = select(func.count(Order.id)).where(
        Order.user_id == user_id, Order.status == "pending"
    )
    if hidden_ids:
        q = q.where(Order.customer_id.notin_(hidden_ids))
    return {"count": (await db.execute(q)).scalar_one()}


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
        **(await _tracking_fields(order, db)),
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


async def _tracking_fields(order, db):
    from app.models.user import User
    from app.services import tracking as trk
    ures = await db.execute(select(User).where(User.id == order.user_id))
    owner = ures.scalar_one_or_none()
    stages = trk.parse_stages(owner.tracking_stages if owner else None)
    cur = trk.current_stage(stages, order.tracking_stage)
    business = ((owner.business_name or owner.full_name) if owner else None) or ""
    return {
        "tracking_code": order.tracking_code,
        "tracking_enabled": bool(order.tracking_enabled),
        "tracking_stage": cur,
        "tracking_next": trk.next_stage(stages, cur),
        "tracking_stages": stages,
        "tracking_message": trk.build_message(business, order.customer_name, order.tracking_code or "", cur),
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
    if new_status == "done" and order.tracking_enabled:
        from app.models.user import User as _U
        from app.services import tracking as _t
        _ur = await db.execute(select(_U).where(_U.id == user_id))
        _ow = _ur.scalar_one_or_none()
        _st = _t.parse_stages(_ow.tracking_stages if _ow else None)
        order.tracking_stage = _st[-1]

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


async def _get_owned_order(db, order_id, user_id):
    result = await db.execute(
        select(Order).where(Order.id == order_id, Order.user_id == user_id)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.patch("/{order_id}/tracking-next")
async def advance_tracking(
    order_id: int,
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    from app.models.user import User
    from app.services import tracking as trk
    order = await _get_owned_order(db, order_id, user_id)
    if not order.tracking_enabled:
        raise HTTPException(status_code=400, detail="Tracking is turned off for this order")
    ures = await db.execute(select(User).where(User.id == user_id))
    owner = ures.scalar_one_or_none()
    stages = trk.parse_stages(owner.tracking_stages if owner else None)
    cur = trk.current_stage(stages, order.tracking_stage)
    nxt = trk.next_stage(stages, cur)
    if not nxt:
        raise HTTPException(status_code=400, detail="This order is already at the last stage")
    order.tracking_stage = nxt
    # Last stage reached: close the order too, but never skip payment
    if trk.next_stage(stages, nxt) is None and order.status in ("paid", "on_the_way"):
        order.status = "done"
        if order.customer_id:
            from app.models.customer import Customer, CustomerType
            cres = await db.execute(select(Customer).where(Customer.id == order.customer_id))
            cust = cres.scalar_one_or_none()
            if cust:
                cust.has_purchased = True
                if cust.customer_type == CustomerType.NEW:
                    cust.customer_type = CustomerType.ACTIVE
    await db.commit()
    fields = await _tracking_fields(order, db)
    return {"success": True, "customer_phone": order.customer_phone, **fields}


@router.patch("/{order_id}/tracking-toggle")
async def toggle_tracking(
    order_id: int,
    request: Request,
    data: dict,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    order = await _get_owned_order(db, order_id, user_id)
    order.tracking_enabled = bool(data.get("enabled"))
    await db.commit()
    return {"success": True, "tracking_enabled": order.tracking_enabled}
