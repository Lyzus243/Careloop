from .customer import Customer
from .base import Base
from .notification import Notification
from .revoked_token import RevokedToken
from .login_attempt import LoginAttempt
from .audit_log import AuditLog
from .sale import Sale
from .subscription import Subscription

try:
    from .user import User
except ImportError:
    from sqlalchemy import Column, Integer, String, DateTime, Boolean
    from sqlalchemy.sql import func
    from .base import Base

    class User(Base):
        __tablename__ = "users"

        id = Column(Integer, primary_key=True, index=True)
        email = Column(String, unique=True, index=True, nullable=False)
        full_name = Column(String, nullable=False)
        business_name = Column(String, nullable=True)
        phone = Column(String, nullable=True)
        is_active = Column(Boolean, default=True)
        created_at = Column(DateTime(timezone=True), server_default=func.now())
        updated_at = Column(DateTime(timezone=True), onupdate=func.now())

__all__ = ["Customer", "User", "Base", "Notification", "RevokedToken", "AuditLog", "Sale", "Subscription"]





