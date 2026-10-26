"""Record one sale when an order's payment is confirmed."""
from datetime import datetime
from decimal import Decimal
from fastapi import HTTPException
from sqlalchemy import select, update
from app.models.order import Order, OrderItem
from app.models.sale import Sale
from app.models.customer import Customer, CustomerType
from app.models.user import User
from app.services.delivery import fee_amount


async def confirm_order_payment(db, user_id, order_id, data):
    result = await db.execute(select(Order).where(Order.id == order_id, Order.user_id == user_id))
    order = result.scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    # Do not backfill old paid orders or recreate deliberately deleted sales.
    if order.status != "pending":
        if order.status not in ("paid", "on_the_way", "done"):
            raise HTTPException(status_code=409, detail="This order cannot be marked paid")
        existing = (await db.execute(select(Sale.id).where(Sale.order_id == order_id, Sale.user_id == user_id))).scalar_one_or_none()
        return {"success": True, "status": order.status, "sale_id": existing, "already_confirmed": True}

    customer = (await db.execute(select(Customer).where(Customer.id == order.customer_id, Customer.user_id == user_id))).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=409, detail="This order no longer has a valid customer. Payment and sales have not been changed.")

    try:
        if order.delivery_type == "pickup":
            fee = Decimal("0.00")
        elif order.delivery_fee_mode == "whatsapp" or order.delivery_fee is None:
            if "delivery_fee" not in data:
                raise ValueError("Enter the agreed delivery fee, including 0 for free delivery")
            fee = fee_amount(data["delivery_fee"])
        else:
            fee = fee_amount(order.delivery_fee)
        total = fee_amount(Decimal(order.subtotal) + fee)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if "expected_total" not in data:
        raise HTTPException(status_code=400, detail="Reload the order and confirm the full payment amount")
    try:
        expected = fee_amount(data["expected_total"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if total != expected:
        raise HTTPException(status_code=409, detail="The order total changed. Reload and verify the amount received.")

    currency = order.currency
    if not currency:
        owner = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
        currency = owner.preferred_currency or "NGN"
    currency = currency.upper()
    if data.get("expected_currency") != currency:
        raise HTTPException(status_code=409, detail="The order currency changed. Reload and verify the payment currency.")

    items = (await db.execute(select(OrderItem).where(OrderItem.order_id == order_id).order_by(OrderItem.id))).scalars().all()
    description = f"Order #{order_id}: " + ", ".join(f"{item.quantity} x {item.product_name_snapshot}" for item in items)
    if fee:
        description += " (includes delivery)"
    description = description[:255]
    # The conditional write serializes simultaneous confirmations on both
    # PostgreSQL and SQLite. The unique index on Sale.order_id is a second guard.
    claimed = await db.execute(
        update(Order).where(Order.id == order_id, Order.user_id == user_id, Order.status == "pending")
        .values(status="paid", total=total, delivery_fee=fee, currency=currency)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        await db.rollback()
        current = (await db.execute(select(Order).where(Order.id == order_id, Order.user_id == user_id))).scalar_one()
        existing = (await db.execute(select(Sale.id).where(Sale.order_id == order_id, Sale.user_id == user_id))).scalar_one_or_none()
        return {"success": True, "status": current.status, "sale_id": existing, "already_confirmed": True}
    sale = Sale(user_id=user_id, customer_id=customer.id, order_id=order_id,
                amount=float(total), currency=currency, product=description, date=datetime.utcnow())
    try:
        db.add(sale)
        customer.has_purchased = True
        if customer.customer_type == CustomerType.NEW:
            customer.customer_type = CustomerType.ACTIVE
        await db.flush()
        sale_id = sale.id
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return {"success": True, "status": "paid", "sale_id": sale_id,
            "total": float(total), "delivery_fee": float(fee), "currency": currency}
