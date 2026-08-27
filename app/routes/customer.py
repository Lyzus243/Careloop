from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user_id
from app.controllers.customer_controller import CustomerController
from app.schemas.customer import (
    CustomerCreate,
    CustomerUpdate,
    CustomerResponse,
    CustomerListResponse
)
from app.models.customer import CustomerType
from app.rate_limit import RateLimitedRouter
from app.services.audit_service import log_action

router = RateLimitedRouter(prefix="/api/customers", tags=["customers"], limit="50/minute", redirect_slashes=False)

@router.post("", response_model=CustomerResponse, status_code=status.HTTP_201_CREATED)
async def create_customer(
    request: Request,
    customer_data: CustomerCreate,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    try:
        customer = await CustomerController.create_customer(db, customer_data, user_id)
        await log_action(db, action="CREATE", resource="customer", user_id=user_id, resource_id=customer.id, ip_address=request.client.host)
        return customer
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create customer: {str(e)}")

@router.get("", response_model=CustomerListResponse)
async def get_customers(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    customer_type: Optional[CustomerType] = Query(None),
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    try:
        return await CustomerController.get_customers(
            db=db, user_id=user_id, page=page, per_page=per_page,
            search=search, customer_type=customer_type
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to fetch customers: {str(e)}")

@router.get("/{customer_id}", response_model=CustomerResponse)
async def get_customer(
    request: Request,
    customer_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    try:
        return await CustomerController.get_customer_by_id(db, customer_id, user_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to fetch customer: {str(e)}")

@router.put("/{customer_id}", response_model=CustomerResponse)
async def update_customer(
    request: Request,
    customer_id: int,
    customer_data: CustomerUpdate,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    try:
        return await CustomerController.update_customer(db, customer_id, customer_data, user_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to update customer: {str(e)}")

@router.delete("/{customer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_customer(
    request: Request,
    customer_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    try:
        await CustomerController.delete_customer(db, customer_id, user_id)
        await log_action(db, action="DELETE", resource="customer", user_id=user_id, resource_id=customer_id, ip_address=request.client.host)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete customer: {str(e)}")




@router.put("/{customer_id}/snooze")
async def snooze_customer(
    customer_id: int,
    days: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Snooze a customer follow-up by updating last_contact"""
    from datetime import timedelta
    customer = await CustomerController.get_customer_by_id(db, customer_id, user_id)
    customer.last_contact = datetime.utcnow() + timedelta(days=days)
    customer.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(customer)
    return {"success": True, "next_followup": customer.last_contact}

@router.post("/{customer_id}/followup-email")
async def send_followup_email(
    customer_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Send a follow-up email to a customer"""
    from app.services.email_service import email_service
    from app.models.user import User
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    business_name = user.business_name if user else "Our Business"
    customer = await CustomerController.get_customer_by_id(db, customer_id, user_id)
    if not customer.email:
        raise HTTPException(status_code=400, detail="Customer has no email address")
    success = email_service.send_followup_email(
        to_email=customer.email,
        customer_name=customer.name,
        business_name=business_name
    )
    return {"success": success}
