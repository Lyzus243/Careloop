from datetime import datetime
from enum import StrEnum
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, String, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class CustomerType(StrEnum):
    NEW = "new"
    ACTIVE = "active"
    INACTIVE = "inactive"


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    date_of_birth: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    customer_type: Mapped[CustomerType] = mapped_column(String(10), default=CustomerType.NEW, nullable=False)
    has_purchased: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_contact: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships - TODO: Add these when Purchase, FollowUp, and CustomerSettings models are created
    # purchases: Mapped[List["Purchase"]] = relationship(
    #     "Purchase",
    #     back_populates="customer",
    #     cascade="all, delete-orphan",
    #     lazy="selectin",
    # )
    # follow_ups: Mapped[List["FollowUp"]] = relationship(
    #     "FollowUp",
    #     back_populates="customer",
    #     cascade="all, delete-orphan",
    #     lazy="selectin",
    # )
    # settings: Mapped["CustomerSettings"] = relationship(
    #     "CustomerSettings",
    #     back_populates="customer",
    #     cascade="all, delete-orphan",
    #     lazy="selectin",
    # )
