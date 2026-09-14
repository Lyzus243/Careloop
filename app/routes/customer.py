from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status, Request, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

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

@router.put("/bulk-categorize")
async def bulk_categorize_customers(
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

@router.put("/{customer_id}/mark-followed-up")
async def mark_followed_up(
    customer_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Mark a customer as followed up today - resets both last_contact and last_followed_up_at."""
    from datetime import datetime
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
    file: UploadFile = File(...),
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Import customers from an uploaded Excel file. Rejects the entire file if any row is invalid."""
    import pandas as pd
    from datetime import datetime as dt
    from app.models.customer import Customer, CustomerType as ModelCustomerType

    try:
        df = pd.read_excel(file.file, sheet_name=0)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read the uploaded file: {str(e)}")

    expected_cols = ["Name", "Phone Number", "Email", "Date of Birth", "Customer Type", "Has Purchased"]
    df.columns = [str(c).strip() for c in df.columns]
    missing_cols = [c for c in expected_cols if c not in df.columns]
    if missing_cols:
        raise HTTPException(status_code=400, detail=f"Missing required columns: {', '.join(missing_cols)}. Please use the provided template.")

    errors = []
    parsed_rows = []

    for idx, row in df.iterrows():
        row_num = idx + 2
        name = str(row.get("Name", "")).strip() if pd.notna(row.get("Name")) else ""
        if not name:
            errors.append(f"Row {row_num}: Name is required")
            continue

        phone = str(row.get("Phone Number", "")).strip() if pd.notna(row.get("Phone Number")) else None
        email = str(row.get("Email", "")).strip() if pd.notna(row.get("Email")) else None

        dob = None
        dob_raw = row.get("Date of Birth")
        if pd.notna(dob_raw):
            try:
                dob = pd.to_datetime(dob_raw).to_pydatetime()
            except Exception:
                errors.append(f"Row {row_num}: Date of Birth '{dob_raw}' is not a valid date")
                continue

        cust_type_raw = str(row.get("Customer Type", "")).strip().lower() if pd.notna(row.get("Customer Type")) else "new"
        if cust_type_raw not in ("new", "active", "inactive"):
            errors.append(f"Row {row_num}: Customer Type must be 'new', 'active', or 'inactive', got '{cust_type_raw}'")
            continue

        has_purchased_raw = str(row.get("Has Purchased", "")).strip().lower() if pd.notna(row.get("Has Purchased")) else "no"
        if has_purchased_raw not in ("yes", "no", "true", "false", ""):
            errors.append(f"Row {row_num}: Has Purchased must be 'yes' or 'no', got '{has_purchased_raw}'")
            continue
        has_purchased = has_purchased_raw in ("yes", "true")

        parsed_rows.append({
            "name": name,
            "phone_number": phone,
            "email": email,
            "date_of_birth": dob,
            "customer_type": cust_type_raw,
            "has_purchased": has_purchased,
        })

    if errors:
        raise HTTPException(status_code=400, detail={"message": "Import rejected due to invalid rows", "errors": errors})

    existing_result = await db.execute(select(Customer).where(Customer.user_id == user_id))
    existing_customers = existing_result.scalars().all()
    existing_by_email = {c.email.strip().lower(): c for c in existing_customers if c.email}

    created = 0
    updated = 0
    skipped = 0

    for row_data in parsed_rows:
        match = existing_by_email.get(row_data["email"].strip().lower()) if row_data["email"] else None
        if match:
            changed = False
            for field in ["name", "phone_number", "date_of_birth", "customer_type", "has_purchased"]:
                new_val = row_data[field]
                old_val = getattr(match, field)
                if new_val not in (None, "") and new_val != old_val:
                    setattr(match, field, new_val)
                    changed = True
            if changed:
                match.updated_at = dt.utcnow()
                updated += 1
            else:
                skipped += 1
        else:
            new_customer = Customer(
                user_id=user_id,
                name=row_data["name"],
                phone_number=row_data["phone_number"],
                email=row_data["email"],
                date_of_birth=row_data["date_of_birth"],
                customer_type=row_data["customer_type"],
                has_purchased=row_data["has_purchased"],
            )
            db.add(new_customer)
            created += 1

    await db.commit()

    return {
        "success": True,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "total_rows": len(parsed_rows)
    }

