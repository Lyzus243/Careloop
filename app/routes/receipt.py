from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user_id
from app.models.order import Order
from app.rate_limit import RateLimitedRouter
from app.services.storage import presigned_download_url, receipt_path

router = RateLimitedRouter(prefix="/api/orders", tags=["orders"], limit="60/minute")


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
