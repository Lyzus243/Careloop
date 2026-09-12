from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text
from sqlalchemy.sql import func
from .base import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    full_name = Column(String, nullable=False)
    business_name = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    hashed_password = Column(String, nullable=True)
    is_email_verified = Column(Boolean, default=False)
    email_verification_token = Column(String, nullable=True)
    email_verification_expires_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    avatar = Column(Text, nullable=True)
    password_reset_token = Column(String, nullable=True)
    password_reset_expires_at = Column(DateTime(timezone=True), nullable=True)
    preferred_currency = Column(String(10), default="USD", nullable=True)
    email_reminders_consent = Column(Boolean, nullable=True)
    last_consent_prompted_at = Column(DateTime(timezone=True), nullable=True)
    last_activity_summary_sent_at = Column(DateTime(timezone=True), nullable=True)
    business_logo = Column(Text, nullable=True)
    followup_message_new = Column(Text, nullable=True)
    followup_message_existing = Column(Text, nullable=True)
    birthday_message = Column(Text, nullable=True)
    custom_new_customer_days = Column(Integer, nullable=True)
    custom_existing_customer_days = Column(Integer, nullable=True)
