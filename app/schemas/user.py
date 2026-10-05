from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime

class UserCreate(BaseModel):
    email: EmailStr
    password: Optional[str] = None
    full_name: Optional[str] = None
    business_name: Optional[str] = None
    phone_number: Optional[str] = None

class UserCreateResponse(BaseModel):
    message: str
    user: Optional[object] = None
    verification_token: Optional[str] = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str
    expires_in: Optional[int] = None
    user: Optional["UserResponse"] = None

class UserResponse(BaseModel):
    preferred_currency: Optional[str] = "USD"
    id: int
    email: EmailStr
    full_name: Optional[str] = None
    business_name: Optional[str] = None
    avatar: Optional[str] = None
    is_active: bool = True
    is_email_verified: bool = False
    email_reminders_consent: Optional[bool] = None
    last_consent_prompted_at: Optional[datetime] = None
    business_logo: Optional[str] = None
    followup_message_new: Optional[str] = None
    followup_message_existing: Optional[str] = None
    birthday_message: Optional[str] = None
    custom_new_customer_days: Optional[int] = None
    custom_existing_customer_days: Optional[int] = None
    last_unresponsive_prompt_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    ai_message_new_enabled: Optional[bool] = False
    ai_message_new_personalize: Optional[bool] = True
    ai_message_new_1: Optional[str] = None
    ai_message_new_2: Optional[str] = None
    ai_message_new_3: Optional[str] = None
    ai_message_new_4: Optional[str] = None
    ai_message_new_5: Optional[str] = None
    ai_message_active_enabled: Optional[bool] = False
    ai_message_active_personalize: Optional[bool] = True
    ai_message_active_1: Optional[str] = None
    ai_message_active_2: Optional[str] = None
    ai_message_active_3: Optional[str] = None
    ai_message_active_4: Optional[str] = None
    ai_message_active_5: Optional[str] = None
    ai_message_inactive_enabled: Optional[bool] = False
    ai_message_inactive_personalize: Optional[bool] = True
    ai_message_inactive_1: Optional[str] = None
    ai_message_inactive_2: Optional[str] = None
    ai_message_inactive_3: Optional[str] = None
    ai_message_inactive_4: Optional[str] = None
    ai_message_inactive_5: Optional[str] = None
    auto_inactive_days: Optional[int] = None

class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    business_name: Optional[str] = None

class EmailVerificationRequest(BaseModel):
    email: EmailStr

class EmailVerificationResponse(BaseModel):
    message: str

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ForgotPasswordResponse(BaseModel):
    message: str

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

class ResetPasswordResponse(BaseModel):
    message: str

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

class ChangePasswordResponse(BaseModel):
    message: str

class SetInitialPasswordRequest(BaseModel):
    token: str
    password: str



# Token.user is a forward reference to UserResponse, declared below it.
Token.model_rebuild()
