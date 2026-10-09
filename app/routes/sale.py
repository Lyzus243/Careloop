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

TOP_N = 5


def _as_date(d):
    """Sale.date may be a date, a datetime or an ISO string; return something with .year/.month."""
    from datetime import datetime
    if d is None:
        return None
    if isinstance(d, str):
        try:
            return datetime.fromisoformat(d[:10]).date()
        except ValueError:
            return None
    return d


def _rank_sales(rows, year, month=0, limit=TOP_N):
    """rows: (Sale, customer_name) pairs. Returns {currency: {"customers": [...], "products": [...]}}.
    Totals are grouped per currency so different currencies are never added together.
    month=0 means the whole year."""
    cust, prod = {}, {}
    for sale, name in rows:
        d = _as_date(sale.date)
        if d is None or d.year != year or (month and d.month != month):
            continue
        cur = sale.currency or ""
        amt = float(sale.amount or 0)
        c = cust.setdefault((cur, sale.customer_id), {"name": name or "Unknown", "total": 0.0, "count": 0})
        c["total"] += amt
        c["count"] += 1
        p_name = (sale.product or "").strip()
        if p_name:
            p = prod.setdefault((cur, p_name.lower()), {"name": p_name, "total": 0.0, "count": 0})
            p["total"] += amt
            p["count"] += 1

    out = {}
    for (cur, _), v in cust.items():
        out.setdefault(cur, {"customers": [], "products": []})["customers"].append(v)
    for (cur, _), v in prod.items():
        out.setdefault(cur, {"customers": [], "products": []})["products"].append(v)
    for cur, d in out.items():
        for k in ("customers", "products"):
            d[k] = sorted(d[k], key=lambda x: (-x["total"], x["name"].lower()))[:limit]
    return out


def _period_label(year, month):
    import calendar
    return f"{calendar.month_name[month]} {year}" if month else f"Whole year {year}"


def _add_ranking_sheets(wb, rows, year, month=0):
    from openpyxl.chart import BarChart, Reference
    from openpyxl.styles import Font, PatternFill, Alignment

    ranks = _rank_sales(rows, year, month)
    label = _period_label(year, month)

    def header(ws, r, titles):
        for ci, t in enumerate(titles, 1):
            c = ws.cell(row=r, column=ci, value=t)
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="3730A3")
            c.alignment = Alignment(horizontal="center")

    def build(sheet_title, key, name_header):
        ws = wb.create_sheet(sheet_title)
        ws.cell(row=1, column=1, value=f"{sheet_title} - {label}").font = Font(bold=True, size=13)
        row = 3
        if not ranks:
            ws.cell(row=row, column=1, value="No sales in this period.")
        for cur in sorted(ranks):
            items = ranks[cur][key]
            if not items:
                continue
            ws.cell(row=row, column=1, value=f"Currency: {cur or 'not set'}").font = Font(bold=True)
            hdr = row + 1
            header(ws, hdr, ["Rank", name_header, "Total", "Purchases"])
            for i, it in enumerate(items, 1):
                r = hdr + i
                ws.cell(row=r, column=1, value=i)
                n = ws.cell(row=r, column=2, value=it["name"])
                n.data_type = "s"
                t = ws.cell(row=r, column=3, value=round(it["total"], 2))
                t.number_format = "#,##0.00"
                ws.cell(row=r, column=4, value=it["count"])
            ch = BarChart()
            ch.type = "bar"
            ch.title = f"{sheet_title} ({cur or 'n/a'})"
            ch.legend = None
            ch.add_data(Reference(ws, min_col=3, min_row=hdr, max_row=hdr + len(items)), titles_from_data=True)
            ch.set_categories(Reference(ws, min_col=2, min_row=hdr + 1, max_row=hdr + len(items)))
            ch.y_axis.scaling.orientation = "maxMin"   # rank 1 at the top
            ch.x_axis.delete = False
            ch.y_axis.delete = False
            ch.y_axis.scaling.min = 0
            ch.height, ch.width = 7.5, 15
            ws.add_chart(ch, f"F{row}")
            row = max(hdr + len(items) + 3, row + 17)
        ws.column_dimensions["A"].width = 8
        ws.column_dimensions["B"].width = 32
        ws.column_dimensions["C"].width = 18
        ws.column_dimensions["D"].width = 11

    build("Top Customers", "customers", "Customer")
    build("Top Products", "products", "Product / Service")


@router.get("/top")
async def top_sales(
    request: Request,
    year: int = 0,
    month: int = 0,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Top customers and products by total spent, for one month or a whole year (month=0)."""
    from datetime import datetime
    from fastapi import HTTPException
    from sqlalchemy import select
    from app.models.sale import Sale
    from app.models.customer import Customer

    if month and not 1 <= month <= 12:
        raise HTTPException(status_code=400, detail="month must be 1-12")
    year = year or datetime.now().year
    result = await db.execute(
        select(Sale, Customer.name)
        .outerjoin(Customer, Customer.id == Sale.customer_id)
        .where(Sale.user_id == user_id)
    )
    ranks = _rank_sales(result.all(), year, month)
    return {
        "year": year,
        "month": month,
        "label": _period_label(year, month),
        "limit": TOP_N,
        "currencies": [
            {"currency": cur or "", "customers": d["customers"], "products": d["products"]}
            for cur, d in sorted(ranks.items())
        ],
    }


@router.get("/export")
async def export_sales(
    request: Request,
    year: int = 0,
    month: int = 0,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Download all of the user's sales as a formatted .xlsx file."""
    import io
    from fastapi.responses import StreamingResponse
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from sqlalchemy import select
    from app.models.sale import Sale
    from app.models.customer import Customer

    result = await db.execute(
        select(Sale, Customer.name)
        .outerjoin(Customer, Customer.id == Sale.customer_id)
        .where(Sale.user_id == user_id)
        .order_by(Sale.date.desc())
    )
    rows = result.all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Sales"
    headers = ["Customer", "Amount", "Currency", "Product/Service", "Date"]
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="3730A3")
        c.alignment = Alignment(horizontal="center", vertical="center")

    for sale, name in rows:
        ws.append([name or "Unknown", float(sale.amount or 0), sale.currency or "", sale.product or "", sale.date])
        r = ws.max_row
        for col in (1, 3, 4):
            cell = ws.cell(row=r, column=col)
            if isinstance(cell.value, str):
                cell.data_type = "s"
        ws.cell(row=r, column=2).number_format = "#,##0.00"
        ws.cell(row=r, column=5).number_format = "yyyy-mm-dd"
        ws.cell(row=r, column=5).alignment = Alignment(horizontal="left")

    for i in range(1, len(headers) + 1):
        longest = max(len(str(ws.cell(row=r, column=i).value or "")) for r in range(1, ws.max_row + 1))
        if i == 2:
            longest += 4
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(max(longest + 3, 12), 50)
    ws.freeze_panes = "A2"

    from datetime import datetime as _dt
    _add_ranking_sheets(wb, rows, year or _dt.now().year, month if 1 <= month <= 12 else 0)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="careloop-sales.xlsx"'}
    )

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
