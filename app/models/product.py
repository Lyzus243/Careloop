from datetime import datetime
from decimal import Decimal

from sqlalchemy import String, Boolean, Integer, Numeric, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    unit_label: Mapped[str | None] = mapped_column(String(60), nullable=True)
    category: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    image_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    is_available_today: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
