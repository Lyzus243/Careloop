from datetime import datetime, date
from decimal import Decimal

from sqlalchemy import String, Integer, Numeric, ForeignKey, DateTime, Text, Boolean, Date
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Order(Base):
    __tablename__ = "orders"
    currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    pickup_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_fee_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="legacy", server_default="legacy")
    delivery_fee: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    delivery_whatsapp: Mapped[str | None] = mapped_column(String(20), nullable=True)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), nullable=True)
    customer_name: Mapped[str] = mapped_column(String(200))
    customer_phone: Mapped[str] = mapped_column(String(40), index=True)
    customer_birthday: Mapped[str | None] = mapped_column(String(10), nullable=True)
    delivery_type: Mapped[str] = mapped_column(String(20))  # "delivery" | "pickup"
    delivery_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    note_to_seller: Mapped[str | None] = mapped_column(Text, nullable=True)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/paid/on_the_way/done
    receipt_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    tracking_code: Mapped[str | None] = mapped_column(String(12), index=True, unique=True, nullable=True)
    tracking_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    tracking_stage: Mapped[str | None] = mapped_column(String(60), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    product_name_snapshot: Mapped[str] = mapped_column(String(200))
    unit_label_snapshot: Mapped[str | None] = mapped_column(String(60), nullable=True)
    unit_price_snapshot: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    quantity: Mapped[int] = mapped_column(Integer)
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2))
