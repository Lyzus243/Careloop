from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import String, DateTime, Integer, ForeignKey, Boolean, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Meta's customer service window: free-form text is only deliverable within 24
# hours of the customer's last inbound message.
SERVICE_WINDOW_HOURS = 24

OPT_OUT_INBOUND = "inbound_stop"
OPT_OUT_MANUAL = "manual"


class WhatsAppContact(Base):
    """Per-business WhatsApp state for a phone number.

    Keyed by phone rather than customer id because Meta webhooks identify people
    by phone number, and because an opt-out must survive a customer record being
    deleted and re-added.
    """

    __tablename__ = "whatsapp_contacts"
    __table_args__ = (
        UniqueConstraint("user_id", "phone_e164", name="uq_wa_contact_user_phone"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    phone_e164: Mapped[str] = mapped_column(String(20), nullable=False)
    customer_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )

    last_inbound_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_outbound_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    opted_out: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    opted_out_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    opt_out_source: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow, nullable=True)

    def in_service_window(self, now: Optional[datetime] = None) -> bool:
        if not self.last_inbound_at:
            return False
        now = now or datetime.utcnow()
        return (now - self.last_inbound_at) < timedelta(hours=SERVICE_WINDOW_HOURS)
