from datetime import datetime
from typing import Optional

from sqlalchemy import String, DateTime, Integer, ForeignKey, Text, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# kind
MSG_TEMPLATE = "template"
MSG_TEXT = "text"
MSG_OPENER = "opener"

# status
MSG_QUEUED = "queued"
MSG_SENDING = "sending"
MSG_SENT = "sent"
MSG_DELIVERED = "delivered"
MSG_READ = "read"
MSG_AWAITING_REPLY = "awaiting_reply"
MSG_REPLIED = "replied"
MSG_FAILED = "failed"
MSG_CANCELLED = "cancelled"
MSG_EXPIRED = "expired"

# Delivery progress ranking. Webhook updates are only applied when they move a
# message forward, which makes redelivered Meta webhooks idempotent.
STATUS_RANK = {
    MSG_QUEUED: 0,
    MSG_SENDING: 1,
    MSG_SENT: 2,
    MSG_DELIVERED: 3,
    MSG_READ: 4,
}

# Statuses that mean the row is finished and must not be touched by the worker.
TERMINAL_STATUSES = {MSG_FAILED, MSG_CANCELLED, MSG_EXPIRED, MSG_REPLIED}

# How long a parked custom message waits for the customer to reply.
OPENER_EXPIRY_DAYS = 7


class WhatsAppMessage(Base):
    """One outbound message. Rows are the work queue for the sending worker."""

    __tablename__ = "whatsapp_messages"
    __table_args__ = (
        Index("ix_wa_msg_status_next_attempt", "status", "next_attempt_at"),
        Index("ix_wa_msg_user_phone_status", "user_id", "to_phone", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Null for single sends from the message modal and for test sends.
    campaign_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("whatsapp_campaigns.id"), nullable=True, index=True
    )
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # SET NULL so deleting a customer never breaks campaign history.
    customer_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    customer_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    to_phone: Mapped[str] = mapped_column(String(20), nullable=False)

    kind: Mapped[str] = mapped_column(String(16), default=MSG_TEXT, nullable=False)
    # For text rows: the message to send. For opener rows: the parked custom
    # message that is delivered once the customer replies.
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(20), default=MSG_QUEUED, nullable=False)
    wa_message_id: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, unique=True, index=True
    )
    # Opener row -> the text row its reply spawned.
    follow_up_message_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("whatsapp_messages.id"), nullable=True
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    error_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=True
    )

    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    failed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow, nullable=True)
