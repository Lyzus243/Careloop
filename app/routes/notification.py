from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from app.database import get_db
from app.dependencies import get_current_user_id
from app.models.notification import Notification
from app.rate_limit import RateLimitedRouter

router = RateLimitedRouter(prefix="/api/notifications", tags=["notifications"], limit="30/minute")

class NotificationCreate(BaseModel):
    type: str
    title: str
    names: Optional[str] = None

class NotificationResponse(BaseModel):
    id: int
    type: str
    title: str
    names: Optional[str]
    is_read: bool
    created_at: datetime

    class Config:
        from_attributes = True

@router.get("", response_model=list[NotificationResponse])
async def get_notifications(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Notification)
        .where(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc())
        .limit(50)
    )
    return result.scalars().all()

@router.post("", response_model=NotificationResponse)
async def create_notification(
    request: Request,
    data: NotificationCreate,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    notif = Notification(user_id=user_id, type=data.type, title=data.title, names=data.names)
    db.add(notif)
    await db.commit()
    await db.refresh(notif)
    return notif

@router.put("/mark-all-read")
async def mark_all_read(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    await db.execute(
        update(Notification)
        .where(Notification.user_id == user_id)
        .values(is_read=True)
    )
    await db.commit()
    return {"success": True}

from app.services.email_service import email_service
from app.models.customer import Customer
from sqlalchemy import and_
from typing import List

class BulkEmailRequest(BaseModel):
    customer_ids: List[int]
    subject: str
    message: str

class BirthdayEmailRequest(BaseModel):
    customer_id: int

@router.post("/bulk-email")
async def send_bulk_email(
    data: BulkEmailRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    from app.models.user import User
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    business_name = user.business_name if user else "Our Business"
    result = await db.execute(
        select(Customer).where(
            and_(Customer.id.in_(data.customer_ids), Customer.user_id == user_id)
        )
    )
    customers = result.scalars().all()
    sent = 0
    failed = 0
    for c in customers:
        if c.email:
            success = email_service.send_bulk_email(
                to_email=c.email,
                customer_name=c.name,
                subject=data.subject,
                message=data.message,
                business_name=business_name
            )
            if success:
                sent += 1
            else:
                failed += 1
    return {"success": True, "sent": sent, "failed": failed, "total": len(customers)}

@router.post("/birthday-email")
async def send_birthday_email(
    data: BirthdayEmailRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    from app.models.user import User
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    business_name = user.business_name if user else "Our Business"
    result = await db.execute(
        select(Customer).where(
            and_(Customer.id == data.customer_id, Customer.user_id == user_id)
        )
    )
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    if not customer.email:
        raise HTTPException(status_code=400, detail="Customer has no email address")
    success = email_service.send_birthday_email(
        to_email=customer.email,
        customer_name=customer.name,
        business_name=business_name
    )
    return {"success": success}
