from fastapi import Depends, Request, UploadFile, File, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user_id
from app.rate_limit import RateLimitedRouter
from app.controllers.product_controller import ProductController
from app.models.product import Product
from app.schemas.product import ProductCreate, ProductUpdate, ProductResponse

router = RateLimitedRouter(prefix="/api/products", tags=["products"], limit="60/minute")


@router.get("", response_model=list[ProductResponse])
async def list_products(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await ProductController.list_for_user(db, user_id)


@router.post("", response_model=ProductResponse)
async def create_product(
    request: Request,
    data: ProductCreate,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await ProductController.create(db, user_id, data)


@router.patch("/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: int,
    request: Request,
    data: ProductUpdate,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await ProductController.update(db, user_id, product_id, data)


@router.delete("/{product_id}", status_code=204)
async def delete_product(
    product_id: int,
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    await ProductController.delete(db, user_id, product_id)


@router.patch("/{product_id}/availability", response_model=ProductResponse)
async def toggle_availability(
    product_id: int,
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await ProductController.toggle_available(db, user_id, product_id)


@router.post("/bulk-import")
async def bulk_import_products(
    request: Request,
    file: UploadFile = File(...),
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Import products from an uploaded Excel file.

    Imports every valid row and reports the invalid ones. Partial imports are
    intentional: a 200-product catalogue with one typo shouldn't require a
    full re-upload."""
    import pandas as pd

    try:
        df = pd.read_excel(file.file, sheet_name=0)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read the uploaded file: {str(e)}")

    expected_cols = ["Name", "Price"]
    df.columns = [str(c).strip() for c in df.columns]
    missing = [c for c in expected_cols if c not in df.columns]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Missing required columns: {', '.join(missing)}. Please use the provided template.",
        )

    errors = []
    products_to_add = []

    for idx, row in df.iterrows():
        row_num = idx + 2  # header is row 1

        name_raw = row.get("Name")
        if pd.isna(name_raw) or not str(name_raw).strip():
            errors.append(f"Row {row_num}: Name is required")
            continue
        name = str(name_raw).strip()[:200]

        price_raw = row.get("Price")
        if pd.isna(price_raw):
            errors.append(f"Row {row_num}: Price is required")
            continue
        try:
            price = float(str(price_raw).replace(",", "").replace("₦", "").strip())
            if price < 0:
                raise ValueError()
        except (ValueError, TypeError):
            errors.append(f"Row {row_num}: Price must be a non-negative number, got '{price_raw}'")
            continue

        unit = None
        if "Unit Label" in df.columns:
            u = row.get("Unit Label")
            if pd.notna(u) and str(u).strip():
                unit = str(u).strip()[:60]

        category = None
        if "Category" in df.columns:
            c = row.get("Category")
            if pd.notna(c) and str(c).strip():
                category = str(c).strip()[:60]

        available = True
        if "Available Today" in df.columns:
            a = row.get("Available Today")
            if pd.notna(a):
                s = str(a).strip().lower()
                if s in ("no", "false", "0", "n"):
                    available = False
                elif s in ("yes", "true", "1", "y", ""):
                    available = True
                else:
                    errors.append(f"Row {row_num}: Available Today must be yes/no, got '{a}'")
                    continue

        products_to_add.append(Product(
            user_id=user_id,
            name=name,
            price=price,
            unit_label=unit,
            category=category,
            is_available_today=available,
        ))

    if products_to_add:
        db.add_all(products_to_add)
        await db.commit()

    return {
        "success": True,
        "inserted": len(products_to_add),
        "skipped": len(errors),
        "errors": errors[:50],  # cap so a badly-broken file doesn't return megabytes
        "errors_truncated": max(0, len(errors) - 50),
    }

@router.post("/bulk-delete")
async def bulk_delete_products(
    request: Request,
    data: dict,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple products by ID, scoped to the current user."""
    from sqlalchemy import delete
    ids = data.get("ids") or []
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="ids must be a non-empty list")
    try:
        ids = [int(i) for i in ids]
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="ids must be integers")
    result = await db.execute(
        delete(Product).where(Product.id.in_(ids), Product.user_id == user_id)
    )
    await db.commit()
    return {"success": True, "deleted": result.rowcount or 0}

