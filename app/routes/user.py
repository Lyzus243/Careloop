from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.controllers.auth_controller import AuthController
from app.schemas.user import UserResponse, UserUpdate
from app.dependencies import get_current_user_id
from app.rate_limit import RateLimitedRouter

router = RateLimitedRouter(prefix="/user", tags=["user"], limit="20/minute")

@router.get("/profile", response_model=UserResponse)
async def get_user_profile(
    request: Request,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    return await AuthController.get_current_user(db, user_id)

@router.put("/profile", response_model=UserResponse)
async def update_user_profile(
    request: Request,
    user_data: UserUpdate,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    return await AuthController.update_user_profile(db, user_id, user_data)

@router.put("/avatar")
async def update_user_avatar(
    request: Request,
    avatar_data: dict,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    avatar_url = avatar_data.get("avatar")
    if not avatar_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Avatar data is required"
        )
    return await AuthController.update_user_avatar(db, user_id, avatar_url)

@router.delete("/account")
async def delete_account(
    data: dict,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Permanently delete the current user's account and all associated data."""
    from sqlalchemy import select, delete
    from app.models.user import User
    from app.models.customer import Customer
    from app.models.sale import Sale
    from app.models.notification import Notification
    from app.models.audit_log import AuditLog

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    confirm_email = data.get("confirm_email", "")
    if confirm_email.strip().lower() != user.email.strip().lower():
        raise HTTPException(status_code=400, detail="Email confirmation does not match")

    await db.execute(delete(Notification).where(Notification.user_id == user_id))
    await db.execute(delete(AuditLog).where(AuditLog.user_id == user_id))
    await db.execute(delete(Sale).where(Sale.user_id == user_id))
    await db.execute(delete(Customer).where(Customer.user_id == user_id))
    await db.execute(delete(User).where(User.id == user_id))
    await db.commit()

    return {"success": True, "message": "Account permanently deleted"}

@router.put("/business-logo")
async def update_business_logo(
    request: Request,
    logo_data: dict,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    logo_url = logo_data.get("business_logo")
    if not logo_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Logo data is required"
        )
    return await AuthController.update_user_business_logo(db, user_id, logo_url)

@router.put("/settings")
async def update_user_settings(
    data: dict,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Update follow-up messages, birthday message, and reminder timing for the current user."""
    from sqlalchemy import select
    from app.models.user import User
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if "followup_message_new" in data:
        user.followup_message_new = data["followup_message_new"] or None
    if "followup_message_existing" in data:
        user.followup_message_existing = data["followup_message_existing"] or None
    if "birthday_message" in data:
        user.birthday_message = data["birthday_message"] or None
    if "custom_new_customer_days" in data:
        val = data["custom_new_customer_days"]
        user.custom_new_customer_days = int(val) if val not in (None, "") else None
    if "custom_existing_customer_days" in data:
        val = data["custom_existing_customer_days"]
        user.custom_existing_customer_days = int(val) if val not in (None, "") else None

    await db.commit()
    return {"success": True}

@router.put("/unresponsive-prompt")
async def mark_unresponsive_prompted(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Record that the unresponsive-customer nudge was just shown."""
    from datetime import datetime
    from sqlalchemy import select
    from app.models.user import User
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.last_unresponsive_prompt_at = datetime.utcnow()
    await db.commit()
    return {"success": True}

@router.put("/consent/prompt")
async def mark_consent_prompted(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Record that the reminder-email consent modal was just shown to this user."""
    from datetime import datetime
    from sqlalchemy import select
    from app.models.user import User
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.last_consent_prompted_at = datetime.utcnow()
    await db.commit()
    return {"success": True, "last_consent_prompted_at": user.last_consent_prompted_at}

@router.put("/consent/set")
async def set_email_reminders_consent(
    data: dict,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Set email reminder consent to true or false from Account Settings."""
    from sqlalchemy import select
    from app.models.user import User
    accepted = bool(data.get("accepted", False))
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.email_reminders_consent = accepted
    await db.commit()
    return {"success": True, "email_reminders_consent": accepted}

@router.put("/consent/accept")
async def accept_email_reminders_consent(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Record that the user accepted the reminder-email consent prompt."""
    from datetime import datetime
    from sqlalchemy import select
    from app.models.user import User
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.email_reminders_consent = True
    user.last_consent_prompted_at = datetime.utcnow()
    await db.commit()
    return {"success": True, "email_reminders_consent": True}
