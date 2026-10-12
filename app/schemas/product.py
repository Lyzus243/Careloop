from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class ProductCreate(BaseModel):
    name: str
    price: Decimal
    unit_label: Optional[str] = None
    category: Optional[str] = None
    is_available_today: bool = True
    sort_order: int = 0


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    price: Optional[Decimal] = None
    unit_label: Optional[str] = None
    category: Optional[str] = None
    is_available_today: Optional[bool] = None
    sort_order: Optional[int] = None


class ProductResponse(BaseModel):
    id: int
    name: str
    price: Decimal
    unit_label: Optional[str]
    category: Optional[str]
    image_key: Optional[str]
    is_available_today: bool
    sort_order: int
    created_at: datetime

    model_config = {"from_attributes": True}
