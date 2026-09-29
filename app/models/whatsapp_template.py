from datetime import datetime
from typing import Optional

from sqlalchemy import String, DateTime, Integer, ForeignKey, Text, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

TEMPLATE_APPROVED = "APPROVED"


class WhatsAppTemplate(Base):
    """Local cache of the message templates defined on a vendor's WABA.

    Meta is the source of truth; this table exists so the compose UI can list
    templates without a Graph round trip and so webhooks can flip a status.
    """

    __tablename__ = "whatsapp_templates"
    __table_args__ = (
        UniqueConstraint("user_id", "name", "language", name="uq_wa_template_user_name_lang"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    waba_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    meta_template_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # POSITIONAL ({{1}}) or NAMED ({{customer_name}})
    parameter_format: Mapped[str] = mapped_column(String(16), default="POSITIONAL", nullable=False)
    body_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    body_param_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    body_param_names: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    header_type: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    components: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
