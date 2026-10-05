from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Subscription(Base):
    """A user's Careloop plan and the Paystack subscription paying for it.

    Users without a row are on the Free plan. All times are naive UTC.
    """
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True, nullable=False)

    # The plan whose limits apply right now.
    plan: Mapped[str] = mapped_column(String(20), default="free", nullable=False)
    # "active" or "past_due" (a renewal failed and the grace period is running).
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    # The paid plan stays in force until this time unless it renews.
    current_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    payment_failed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_payment_reminder_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    paystack_customer_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    paystack_authorization_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    paystack_subscription_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    paystack_email_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # A downgrade or cancellation waiting for the end of the paid period.
    # pending_plan "free" means the subscription was cancelled.
    pending_plan: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    pending_subscription_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    pending_email_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, onupdate=datetime.utcnow)
