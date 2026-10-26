from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class SaleCreate(BaseModel):
    customer_id: int
    amount: float
    currency: str = "USD"
    product: Optional[str] = None
    date: Optional[datetime] = None

class SaleResponse(BaseModel):
    model_config = {"from_attributes": True}

    order_id: Optional[int] = None
    id: int
    user_id: int
    customer_id: int
    amount: float
    currency: str
    product: Optional[str] = None
    date: datetime

class SaleListResponse(BaseModel):
    items: list[SaleResponse]
    total: int

class UserPreferencesUpdate(BaseModel):
    preferred_currency: Optional[str] = None
