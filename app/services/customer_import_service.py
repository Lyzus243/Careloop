"""Shared save-and-dedupe step for every customer import source.

Excel uploads, pasted contact lists, vCard files and the phone contact picker
all produce plain row dicts and hand them to ``import_customer_rows``.
Each row is validated on its own: good rows are saved, bad rows are reported
back with a reason instead of rejecting the whole import.
"""
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.utils.phone import normalize_phone

VALID_TYPES = ("new", "active", "inactive")
TRUE_VALUES = ("yes", "true", "y", "1")
FALSE_VALUES = ("no", "false", "n", "0", "")


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _parse_date(value: Any) -> Optional[datetime]:
    if _blank(value):
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y%m%d", "%d.%m.%Y", "--%m-%d", "--%m%d"):
        try:
            parsed = datetime.strptime(text, fmt)
            # vCards may omit the birth year ("--0415"); store it on a neutral year.
            if fmt.startswith("--"):
                parsed = parsed.replace(year=1900)
            return parsed
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        raise ValueError(f"'{text}' is not a valid date")


def _validate_row(row: dict, default_dial_code: str) -> dict:
    """Return a cleaned row, or raise ValueError with a reason the owner can act on."""
    name = str(row.get("name") or "").strip()
    if not name:
        raise ValueError("Name is required")
    if len(name) > 255:
        raise ValueError("Name is too long")

    raw_phone = row.get("phone_number")
    phone = None
    if not _blank(raw_phone):
        phone = normalize_phone(str(raw_phone), default_dial_code)
        if not phone:
            raise ValueError(f"'{raw_phone}' is not a valid phone number")

    email = None if _blank(row.get("email")) else str(row["email"]).strip()
    if email and ("@" not in email or len(email) > 255):
        raise ValueError(f"'{email}' is not a valid email")

    if not phone and not email:
        raise ValueError("A phone number or email is required")

    customer_type = None
    if not _blank(row.get("customer_type")):
        customer_type = str(row["customer_type"]).strip().lower()
        if customer_type == "existing":
            customer_type = "active"
        if customer_type not in VALID_TYPES:
            raise ValueError("Customer Type must be 'new', 'active', or 'inactive'")

    has_purchased = None
    raw_purchased = row.get("has_purchased")
    if isinstance(raw_purchased, bool):
        has_purchased = raw_purchased
    elif raw_purchased is not None:
        text = str(raw_purchased).strip().lower()
        if text in TRUE_VALUES:
            has_purchased = True
        elif text in FALSE_VALUES:
            has_purchased = False if text else None
        else:
            raise ValueError("Has Purchased must be 'yes' or 'no'")

    return {
        "name": name,
        "phone_number": phone,
        "email": email,
        "date_of_birth": _parse_date(row.get("date_of_birth")),
        "customer_type": customer_type,
        "has_purchased": has_purchased,
    }


async def import_customer_rows(
    db: AsyncSession,
    user_id: int,
    rows: list[dict],
    default_dial_code: str = "+234",
    overwrite: bool = False,
    row_labels: Optional[list[str]] = None,
    max_new: Optional[int] = None,
) -> dict:
    """Validate, dedupe and save rows for one owner.

    A row matches an existing customer by phone number first, then by email.
    With ``overwrite`` (Excel uploads) matched customers take the imported values.
    Without it (quick imports) only fields that are empty on the customer are filled in,
    so importing phone contacts never renames or recategorises someone.
    ``max_new`` caps how many new customers are created (the plan's remaining slots);
    rows past it are reported in ``failed``. Updates to existing customers always go through.
    """
    existing = (await db.execute(select(Customer).where(Customer.user_id == user_id))).scalars().all()
    by_phone: dict[str, Customer] = {}
    by_email: dict[str, Customer] = {}
    for c in existing:
        key = normalize_phone(c.phone_number, default_dial_code) if c.phone_number else None
        if key:
            by_phone.setdefault(key, c)
        if c.email:
            by_email.setdefault(c.email.strip().lower(), c)

    created = updated = skipped = 0
    limit_reached = False
    failed: list[dict] = []
    new_ids: set[int] = set()  # id() of customers created in this import

    for index, raw in enumerate(rows):
        label = row_labels[index] if row_labels else f"Row {index + 1}"
        try:
            data = _validate_row(raw, default_dial_code)
        except ValueError as e:
            failed.append({"row": label, "name": str(raw.get("name") or "").strip(), "reason": str(e)})
            continue

        email_key = data["email"].lower() if data["email"] else None
        match = (by_phone.get(data["phone_number"]) if data["phone_number"] else None) or (
            by_email.get(email_key) if email_key else None
        )

        if match is None:
            if max_new is not None and created >= max_new:
                failed.append({"row": label, "name": data["name"], "reason": "Over your plan's customer limit. Upgrade to add more."})
                limit_reached = True
                continue
            customer = Customer(
                user_id=user_id,
                name=data["name"],
                phone_number=data["phone_number"],
                email=data["email"],
                date_of_birth=data["date_of_birth"],
                customer_type=data["customer_type"] or "new",
                has_purchased=bool(data["has_purchased"]),
            )
            db.add(customer)
            new_ids.add(id(customer))
            created += 1
        else:
            customer = match
            if id(customer) in new_ids:
                # Same person listed twice in this import.
                skipped += 1
                continue
            changed = False
            for field, value in data.items():
                if value is None:
                    continue
                current = getattr(customer, field)
                if overwrite:
                    should_set = value != current
                else:
                    should_set = _blank(current)
                if should_set:
                    setattr(customer, field, value)
                    changed = True
            if changed:
                customer.updated_at = datetime.utcnow()
                updated += 1
            else:
                skipped += 1

        if customer.phone_number:
            by_phone.setdefault(normalize_phone(customer.phone_number, default_dial_code) or "", customer)
        if customer.email:
            by_email.setdefault(customer.email.strip().lower(), customer)

    await db.commit()
    return {
        "success": True,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
        "total_rows": len(rows),
        "limit_reached": limit_reached,
    }
