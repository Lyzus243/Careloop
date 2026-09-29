from datetime import datetime
from typing import Optional

from sqlalchemy import String, DateTime, Integer, ForeignKey, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Status values for whatsapp_accounts.status
ACCOUNT_CONNECTED = "connected"
ACCOUNT_ERROR = "error"
ACCOUNT_DISCONNECTED = "disconnected"

# The opener template Careloop creates on every vendor's WABA so that vendors can
# send custom (non-template) messages to customers outside the 24-hour window.
OPENER_TEMPLATE_NAME = "careloop_message_opener"
OPENER_TEMPLATE_LANGUAGE = "en"


class WhatsAppAccount(Base):
    """One connected WhatsApp Business Account per Careloop user (the business)."""

    __tablename__ = "whatsapp_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True
    )

    waba_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    phone_number_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    display_phone_number: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    verified_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    quality_rating: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    # Fernet ciphertext. Never returned by the API, never logged.
    access_token_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    token_issued_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    status: Mapped[str] = mapped_column(String(16), default=ACCOUNT_CONNECTED, nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    app_subscribed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    phone_registered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    templates_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    opener_template_name: Mapped[str] = mapped_column(
        String(512), default=OPENER_TEMPLATE_NAME, nullable=False
    )
    opener_template_language: Mapped[str] = mapped_column(
        String(16), default=OPENER_TEMPLATE_LANGUAGE, nullable=False
    )
    # PENDING / APPROVED / REJECTED / None (never submitted)
    opener_template_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    connected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    disconnected_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow, nullable=True)

    @property
    def is_connected(self) -> bool:
        return self.status == ACCOUNT_CONNECTED and bool(self.access_token_encrypted)
