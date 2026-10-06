from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status, Request, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.database import get_db
from app.dependencies import get_current_user_id
from app.controllers.customer_controller import CustomerController
from app.schemas.customer import (
    CustomerCreate,
    CustomerUpdate,
    CustomerResponse,
    CustomerListResponse,
    CustomerImportRequest,
)
from app.models.customer import CustomerType
from app.rate_limit import RateLimitedRouter
from app.services.audit_service import log_action
from app.services.customer_import_service import import_customer_rows
from app.services.billing_service import ensure_can_add_customer, remaining_customer_slots

router = RateLimitedRouter(prefix="/api/customers", tags=["customers"], limit="50/minute", redirect_slashes=False)

@router.post("", response_model=CustomerResponse, status_code=status.HTTP_201_CREATED)
async def create_customer(
    request: Request,
    customer_data: CustomerCreate,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    try:
        await ensure_can_add_customer(db, user_id)
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

@router.put("/bulk-categorize")
async def bulk_categorize_customers(
    request: Request,
    data: dict,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Bulk update customer_type for multiple customers at once."""
    from app.models.customer import Customer
    from datetime import datetime as dt

    customer_ids = data.get("customer_ids", [])
    new_type = data.get("customer_type", "")

    if new_type not in ("new", "active", "inactive"):
        raise HTTPException(status_code=400, detail="customer_type must be 'new', 'active', or 'inactive'")
    if not customer_ids:
        raise HTTPException(status_code=400, detail="No customers selected")

    result = await db.execute(
        select(Customer).where(
            and_(Customer.id.in_(customer_ids), Customer.user_id == user_id)
        )
    )
    customers = result.scalars().all()

    updated = 0
    for c in customers:
        c.customer_type = new_type
        c.updated_at = dt.utcnow()
        updated += 1

    await db.commit()
    return {"success": True, "updated": updated}


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

@router.delete("/bulk-delete")
async def bulk_delete_customers(
    request: Request,
    data: dict,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Delete multiple customers and their related sales records at once."""
    from sqlalchemy import delete
    from app.models.customer import Customer
    from app.models.sale import Sale

    customer_ids = data.get("customer_ids", [])
    if not customer_ids:
        raise HTTPException(status_code=400, detail="No customers selected")

    result = await db.execute(
        select(Customer).where(
            and_(Customer.id.in_(customer_ids), Customer.user_id == user_id)
        )
    )
    owned_customers = result.scalars().all()
    owned_ids = [c.id for c in owned_customers]

    if not owned_ids:
        raise HTTPException(status_code=404, detail="No matching customers found")

    await db.execute(delete(Sale).where(Sale.customer_id.in_(owned_ids)))
    await db.execute(delete(Customer).where(Customer.id.in_(owned_ids)))
    await db.commit()

    for cid in owned_ids:
        await log_action(db, action="DELETE", resource="customer", user_id=user_id, resource_id=cid, ip_address=request.client.host)

    return {"success": True, "deleted": len(owned_ids)}

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
    request: Request,
    customer_id: int,
    days: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Snooze a customer follow-up by updating last_contact"""
    customer = await CustomerController.get_customer_by_id(db, customer_id, user_id)
    customer.last_contact = datetime.utcnow() + timedelta(days=days)
    customer.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(customer)
    return {"success": True, "next_followup": customer.last_contact}

@router.put("/{customer_id}/mark-followed-up")
async def mark_followed_up(
    request: Request,
    customer_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Mark a customer as followed up today - resets both last_contact and last_followed_up_at."""
    customer = await CustomerController.get_customer_by_id(db, customer_id, user_id)
    now = datetime.utcnow()
    customer.last_contact = now
    customer.last_followed_up_at = now
    customer.updated_at = now
    await db.commit()
    await db.refresh(customer)
    return {"success": True, "last_followed_up_at": customer.last_followed_up_at}

@router.post("/bulk-import")
async def bulk_import_customers(
    request: Request,
    file: UploadFile = File(...),
    default_dial_code: str = Form("+234"),
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Import customers from an uploaded Excel file.

    Only the Name column is required. Valid rows are saved and invalid rows are
    returned in ``failed`` with their spreadsheet row number and a reason.
    """
    import pandas as pd

    try:
        df = pd.read_excel(file.file, sheet_name=0, dtype=object)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read the uploaded file: {str(e)}")

    df.columns = [str(c).strip() for c in df.columns]
    if "Name" not in df.columns:
        raise HTTPException(status_code=400, detail="Missing the 'Name' column. Please use the provided template.")

    columns = {
        "Name": "name",
        "Phone Number": "phone_number",
        "Email": "email",
        "Date of Birth": "date_of_birth",
        "Customer Type": "customer_type",
        "Has Purchased": "has_purchased",
    }

    rows, labels = [], []
    for idx, row in df.iterrows():
        values = {}
        for col, field in columns.items():
            value = row.get(col) if col in df.columns else None
            if value is None or (not isinstance(value, str) and pd.isna(value)):
                value = None
            elif hasattr(value, "to_pydatetime"):
                value = value.to_pydatetime()
            elif isinstance(value, float) and value.is_integer():
                value = int(value)
            if field in ("name", "phone_number", "email", "customer_type") and value is not None and not isinstance(value, str):
                value = str(value)
            values[field] = value
        if all(v is None for v in values.values()):
            continue  # fully blank spreadsheet row
        rows.append(values)
        labels.append(f"Row {idx + 2}")

    if not rows:
        raise HTTPException(status_code=400, detail="The file has no customer rows.")

    result = await import_customer_rows(
        db, user_id, rows, default_dial_code=default_dial_code, overwrite=True, row_labels=labels,
        max_new=await remaining_customer_slots(db, user_id),
    )
    await log_action(
        db, action="IMPORT", resource="customer", user_id=user_id,
        ip_address=request.client.host if request.client else None,
        detail=f"created={result['created']} updated={result['updated']} skipped={result['skipped']} failed={len(result['failed'])}",
    )
    return result


@router.post("/import-rows")
async def import_customer_rows_endpoint(
    request: Request,
    payload: CustomerImportRequest,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Import contacts from a pasted list, vCard file or the phone contact picker.

    Existing customers are matched by phone number, then email, and only have
    empty fields filled in, so re-importing the same contacts is safe.
    """
    rows = [r.model_dump() for r in payload.rows]
    result = await import_customer_rows(
        db, user_id, rows, default_dial_code=payload.default_dial_code, overwrite=False,
        max_new=await remaining_customer_slots(db, user_id),
    )
    await log_action(
        db, action="IMPORT", resource="customer", user_id=user_id,
        ip_address=request.client.host if request.client else None,
        detail=f"created={result['created']} updated={result['updated']} skipped={result['skipped']} failed={len(result['failed'])}",
    )
    return result
