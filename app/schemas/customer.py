from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from enum import Enum

class CustomerType(str, Enum):
    active = "active"
    new = "new"

class CustomerCreate(BaseModel):
    name: str
    phone_number: Optional[str] = None
    email: Optional[str] = None
    date_of_birth: Optional[datetime] = None
    customer_type: Optional[CustomerType] = CustomerType.new
    has_purchased: Optional[bool] = False

class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    phone_number: Optional[str] = None
    email: Optional[str] = None
    date_of_birth: Optional[datetime] = None
    customer_type: Optional[CustomerType] = None
    has_purchased: Optional[bool] = None
    last_contact: Optional[datetime] = None

class CustomerResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    name: str
    phone_number: Optional[str] = None
    email: Optional[str] = None
    date_of_birth: Optional[datetime] = None
    customer_type: CustomerType = CustomerType.new
    has_purchased: bool = False
    last_contact: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    user_id: int

class CustomerListResponse(BaseModel):
    items: list[CustomerResponse]
    total: int
    page: int
    per_page: int
    total_pages: int
