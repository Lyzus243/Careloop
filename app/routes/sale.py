from fastapi import APIRouter, Depends, HTTPException, status, Request, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user_id
from app.controllers.sale_controller import SaleController
from app.controllers.auth_controller import AuthController
from app.schemas.sale import SaleCreate, SaleListResponse, SaleResponse, UserPreferencesUpdate
from app.rate_limit import RateLimitedRouter

router = RateLimitedRouter(prefix="/api/sales", tags=["sales"], limit="30/minute", redirect_slashes=False)

@router.post("", response_model=SaleResponse, status_code=status.HTTP_201_CREATED)
async def create_sale(
    request: Request,
    data: SaleCreate,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    return await SaleController.create_sale(db, user_id, data)

@router.get("", response_model=SaleListResponse)
async def get_sales(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    return await SaleController.get_sales(db, user_id)

@router.delete("/{sale_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sale(
    request: Request,
    sale_id: int,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    await SaleController.delete_sale(db, sale_id, user_id)

@router.post("/bulk-import")
async def bulk_import_sales(
    request: Request,
    file: UploadFile = File(...),
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Import sales from an uploaded Excel file. Rejects the entire file if any row is invalid."""
    import pandas as pd
    from sqlalchemy import select
    from app.models.customer import Customer
    from app.models.sale import Sale

    try:
        df = pd.read_excel(file.file, sheet_name=0)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read the uploaded file: {str(e)}")

    expected_cols = ["Customer Email", "Amount", "Currency", "Product", "Date"]
    df.columns = [str(c).strip() for c in df.columns]
    missing_cols = [c for c in expected_cols if c not in df.columns]
    if missing_cols:
        raise HTTPException(status_code=400, detail=f"Missing required columns: {', '.join(missing_cols)}. Please use the provided template.")

    customers_result = await db.execute(select(Customer).where(Customer.user_id == user_id))
    customers_by_email = {c.email.strip().lower(): c for c in customers_result.scalars().all() if c.email}

    errors = []
    parsed_rows = []

    for idx, row in df.iterrows():
        row_num = idx + 2
        email = str(row.get("Customer Email", "")).strip().lower() if pd.notna(row.get("Customer Email")) else ""
        if not email:
            errors.append(f"Row {row_num}: Customer Email is required")
            continue
        customer = customers_by_email.get(email)
        if not customer:
            errors.append(f"Row {row_num}: No customer found with email '{email}'")
            continue

        amount_raw = row.get("Amount")
        if pd.isna(amount_raw):
            errors.append(f"Row {row_num}: Amount is required")
            continue
        try:
            amount = float(amount_raw)
            if amount <= 0:
                raise ValueError()
        except (ValueError, TypeError):
            errors.append(f"Row {row_num}: Amount must be a positive number, got '{amount_raw}'")
            continue

        currency = str(row.get("Currency", "")).strip().upper() if pd.notna(row.get("Currency")) else "USD"
        if not currency:
            currency = "USD"

        product = str(row.get("Product", "")).strip() if pd.notna(row.get("Product")) else None

        date_raw = row.get("Date")
        if pd.notna(date_raw):
            try:
                sale_date = pd.to_datetime(date_raw).to_pydatetime()
            except Exception:
                errors.append(f"Row {row_num}: Date '{date_raw}' is not a valid date")
                continue
        else:
            from datetime import datetime as dt
            sale_date = dt.utcnow()

        parsed_rows.append({
            "customer_id": customer.id,
            "amount": amount,
            "currency": currency,
            "product": product,
            "date": sale_date,
        })

    if errors:
        raise HTTPException(status_code=400, detail={"message": "Import rejected due to invalid rows", "errors": errors})

    created = 0
    for row_data in parsed_rows:
        new_sale = Sale(
            user_id=user_id,
            customer_id=row_data["customer_id"],
            amount=row_data["amount"],
            currency=row_data["currency"],
            product=row_data["product"],
            date=row_data["date"],
        )
        db.add(new_sale)
        created += 1

    await db.commit()
    return {"success": True, "created": created}

@router.put("/preferences")
async def update_preferences(
    request: Request,
    data: UserPreferencesUpdate,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    user = await AuthController._get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if data.preferred_currency:
        user.preferred_currency = data.preferred_currency
    await db.commit()
    return {"message": "Preferences updated", "preferred_currency": user.preferred_currency}
