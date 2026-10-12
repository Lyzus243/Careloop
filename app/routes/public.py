import json
from decimal import Decimal
from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.customer import Customer, CustomerType
from app.models.order import Order, OrderItem
from app.models.product import Product
from app.models.user import User
from app.services.phone import normalise_phone
from app.services.storage import save_receipt, presigned_upload_url, presigned_download_url, receipt_path, backend_name
from app.rate_limit import RateLimitedRouter

from datetime import datetime as _dt


def _parse_dob(s):
    """Parse 'YYYY-MM-DD' into a datetime, or return None if invalid/empty."""
    if not s:
        return None
    try:
        return _dt.strptime(str(s).strip()[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


public_router = RateLimitedRouter(prefix="/api/public", tags=["public"], limit="60/minute")


@public_router.get("/{slug}")
async def get_public_business(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
):
    """Public payload for the order page. No auth. Only the business name,
    products, prices and bank details — nothing else about the business."""
    result = await db.execute(select(User).where(User.slug == slug))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Business not found")

    products_result = await db.execute(
        select(Product)
        .where(Product.user_id == user.id)
        .order_by(Product.sort_order.asc(), Product.created_at.asc())
    )
    products = products_result.scalars().all()

    return {
        "business_name": user.business_name or user.full_name or "Business",
        "business_logo": user.business_logo,
        "business_type": user.business_type,
        "products": [
            {
                "id": p.id,
                "name": p.name,
                "price": float(p.price),
                "unit_label": p.unit_label,
                "category": p.category,
                "image_key": p.image_key,
                "is_available_today": bool(p.is_available_today),
            }
            for p in products
        ],
        "bank": {
            "bank_name": user.bank_name,
            "account_number": user.bank_account_number,
            "account_name": user.bank_account_name,
        },
    }


@public_router.post("/{slug}/orders")
async def submit_public_order(
    request: Request,
    slug: str,
    data: str = Form(...),
    receipt: UploadFile = File(None),
    db: AsyncSession = Depends(get_db),
):
    """Receive an order from the public page. Rejects if any item is unavailable
    or its price changed since the customer loaded the page."""
    try:
        payload = json.loads(data)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid order data")

    result = await db.execute(select(User).where(User.slug == slug))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Business not found")

    name = (payload.get("customer_name") or "").strip()
    phone_raw = (payload.get("customer_phone") or "").strip()
    birthday = (payload.get("customer_birthday") or "").strip() or None
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    if not phone_raw:
        raise HTTPException(status_code=400, detail="Phone number is required")
    phone = normalise_phone(phone_raw)
    if len(phone) < 8:
        raise HTTPException(status_code=400, detail="Enter a valid phone number")

    delivery_type = (payload.get("delivery_type") or "").strip().lower()
    if delivery_type not in ("delivery", "pickup"):
        raise HTTPException(status_code=400, detail="Choose delivery or pickup")
    address = (payload.get("delivery_address") or "").strip() or None
    if delivery_type == "delivery" and not address:
        raise HTTPException(status_code=400, detail="Address is required for delivery")

    note = (payload.get("note_to_seller") or "").strip() or None
    items_in = payload.get("items") or []
    if not items_in:
        raise HTTPException(status_code=400, detail="Your cart is empty")

    ids = []
    for it in items_in:
        try:
            ids.append(int(it.get("product_id")))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid product in cart")

    rows = await db.execute(
        select(Product).where(Product.id.in_(ids), Product.user_id == user.id)
    )
    by_id = {p.id: p for p in rows.scalars().all()}

    order_items_data = []
    subtotal = Decimal("0.00")
    for it in items_in:
        pid = int(it.get("product_id"))
        try:
            qty = int(it.get("quantity"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid quantity")
        if qty < 1 or qty > 100:
            raise HTTPException(status_code=400, detail="Quantity must be between 1 and 100")
        prod = by_id.get(pid)
        if not prod:
            raise HTTPException(status_code=400, detail=f"A product is no longer available")
        if not prod.is_available_today:
            raise HTTPException(status_code=409, detail=f"'{prod.name}' is not available right now. Please review your cart.")
        expected = it.get("expected_price")
        if expected is not None:
            try:
                if Decimal(str(expected)) != Decimal(prod.price):
                    raise HTTPException(status_code=409, detail=f"The price of '{prod.name}' changed. Please review your cart.")
            except HTTPException:
                raise
            except Exception:
                pass
        line = Decimal(prod.price) * qty
        subtotal += line
        order_items_data.append({
            "product_id": prod.id,
            "product_name_snapshot": prod.name,
            "unit_label_snapshot": prod.unit_label,
            "unit_price_snapshot": Decimal(prod.price),
            "quantity": qty,
            "line_total": line,
        })

    # Receipt: accept either an uploaded file (multipart) or a key from a
    # prior presigned upload. Exactly one must be provided.
    receipt_key = (payload.get("receipt_key") or "").strip() or None
    if receipt_key:
        # Sanity: must look like one of our keys
        if not receipt_key.startswith("receipts/") or ".." in receipt_key:
            raise HTTPException(status_code=400, detail="Invalid receipt reference")
    elif receipt is not None:
        contents = await receipt.read()
        try:
            receipt_key = save_receipt(receipt.filename or "", contents)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    else:
        raise HTTPException(status_code=400, detail="Receipt is required")

    # Customer upsert
    cust_row = await db.execute(
        select(Customer).where(Customer.user_id == user.id, Customer.phone_number == phone)
    )
    customer = cust_row.scalar_one_or_none()
    if customer is None:
        # Best-effort: also try the raw form in case older records aren't normalised
        cust_row = await db.execute(
            select(Customer).where(Customer.user_id == user.id, Customer.phone_number == phone_raw)
        )
        customer = cust_row.scalar_one_or_none()

    parsed_dob = _parse_dob(birthday)
    if customer is None:
        customer = Customer(
            user_id=user.id,
            name=name,
            phone_number=phone,
            customer_type=CustomerType.NEW,
        )
        if parsed_dob:
            customer.date_of_birth = parsed_dob
        db.add(customer)
        await db.flush()
    else:
        if not customer.name:
            customer.name = name
        if parsed_dob and not customer.date_of_birth:
            customer.date_of_birth = parsed_dob

    order = Order(
        user_id=user.id,
        customer_id=customer.id,
        customer_name=name,
        customer_phone=phone,
        customer_birthday=birthday,
        delivery_type=delivery_type,
        delivery_address=address,
        note_to_seller=note,
        subtotal=subtotal,
        total=subtotal,
        status="pending",
        receipt_key=receipt_key,
    )
    db.add(order)
    await db.flush()

    for od in order_items_data:
        db.add(OrderItem(order_id=order.id, **od))

    await db.commit()
    return {"success": True, "order_id": order.id}


@public_router.post("/{slug}/receipt-url")
async def request_receipt_upload_url(
    request: Request,
    slug: str,
    data: dict,
    db: AsyncSession = Depends(get_db),
):
    """Return a presigned PUT URL so the browser can upload the receipt
    directly to storage, bypassing the API. Falls back with 501 on the local
    backend so the client can use multipart upload instead."""
    result = await db.execute(select(User).where(User.slug == slug))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Business not found")

    if backend_name() != "s3":
        raise HTTPException(status_code=501, detail="Presigned uploads not available")

    filename = (data.get("filename") or "").strip()
    content_type = (data.get("content_type") or "").strip().lower()
    try:
        url, key = presigned_upload_url(filename, content_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"upload_url": url, "key": key, "expires_in": 600}
