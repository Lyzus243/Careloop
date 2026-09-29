from datetime import datetime
from typing import Optional

from sqlalchemy import String, DateTime, Integer, ForeignKey, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# kind
KIND_TEMPLATE = "template"
KIND_CUSTOM = "custom"

# status
CAMPAIGN_QUEUED = "queued"
CAMPAIGN_SENDING = "sending"
CAMPAIGN_COMPLETED = "completed"
CAMPAIGN_CANCELLED = "cancelled"
CAMPAIGN_FAILED = "failed"


class WhatsAppCampaign(Base):
    """One bulk send. Either an approved template blast or a custom message.

    A custom campaign splits its recipients: those who messaged the business in
    the last 24 hours receive the text directly, everyone else first receives the
    opener template and the text is delivered when they reply.
    """

    __tablename__ = "whatsapp_campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    account_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("whatsapp_accounts.id"), nullable=True
    )

    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    audience: Mapped[str] = mapped_column(String(16), default="selected", nullable=False)

    template_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    template_language: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    # [{"source": "customer_name"|"static", "value": "..."}]
    template_params: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    custom_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(16), default=CAMPAIGN_QUEUED, nullable=False)

    total_selected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_recipients: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    direct_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    opener_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    sent_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    delivered_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    read_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    replied_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    awaiting_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expired_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    skipped_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # {"no_phone": n, "invalid_phone": n, "opted_out": n}
    skipped: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
