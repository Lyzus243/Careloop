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

    # Optional image-URL helper
    import asyncio, urllib.request

    def _fetch_image_sync(url):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Careloop/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                ct = (resp.headers.get("Content-Type") or "").lower()
                if not ct.startswith("image/"):
                    return None, "not an image"
                data = resp.read(2 * 1024 * 1024 + 1)
                if len(data) > 2 * 1024 * 1024:
                    return None, "image larger than 2MB"
                return data, None
        except Exception as e:
            return None, str(e)[:80]

    async def _fetch_image(url):
        return await asyncio.to_thread(_fetch_image_sync, url)

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

        image_url = None
        if "Image URL" in df.columns:
            iu = row.get("Image URL")
            if pd.notna(iu) and str(iu).strip():
                image_url = str(iu).strip()
        products_to_add.append({
            "user_id": user_id,
            "name": name,
            "price": price,
            "unit_label": unit,
            "category": category,
            "is_available_today": available,
            "_image_url": image_url,
        })

    if products_to_add:
        from app.services.storage import save_product_image
        rows_with_urls = [d for d in products_to_add if d.get("_image_url")]
        for d in products_to_add:
            d.pop("_image_url", None)

        objs = [Product(**d) for d in products_to_add]
        db.add_all(objs)
        await db.flush()

        # Fetch and attach images (best-effort; never fails the import)
        for obj, d, orig in zip(objs, products_to_add, rows_with_urls):
            pass  # placeholder; real loop below
        url_map = {}
        for orig in rows_with_urls:
            pass

        # Match by position: rebuild the map
        url_list = [d.get("_image_url") for d in products_to_add]

        for obj, url in zip(objs, url_list):
            if not url:
                continue
            data, err = await _fetch_image(url)
            if err or not data:
                errors.append(f"Image for '{obj.name}': {err or 'could not fetch'}")
                continue
            try:
                ext = ".jpg"
                for cand in (".jpg", ".jpeg", ".png"):
                    if url.lower().endswith(cand):
                        ext = cand
                        break
                key = save_product_image("uploaded" + ext, data)
                obj.image_key = key
            except ValueError as e:
                errors.append(f"Image for '{obj.name}': {e}")

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


@router.post("/{product_id}/image")
async def upload_product_image(
    product_id: int,
    request: Request,
    file: UploadFile = File(...),
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Attach or replace a product image. Replaces any existing image."""
    from sqlalchemy import select
    from app.services.storage import save_product_image, delete_product_image
    from app.schemas.product import ProductResponse

    result = await db.execute(
        select(Product).where(Product.id == product_id, Product.user_id == user_id)
    )
    p = result.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")

    data = await file.read()
    try:
        key = save_product_image(file.filename or "", data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    old_key = p.image_key
    p.image_key = key
    await db.commit()
    await db.refresh(p)
    if old_key and old_key != key:
        delete_product_image(old_key)
    return ProductResponse.model_validate(p)


@router.delete("/{product_id}/image", status_code=204)
async def delete_product_image_route(
    product_id: int,
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select
    from app.services.storage import delete_product_image

    result = await db.execute(
        select(Product).where(Product.id == product_id, Product.user_id == user_id)
    )
    p = result.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    if p.image_key:
        key = p.image_key
        p.image_key = None
        await db.commit()
        delete_product_image(key)
