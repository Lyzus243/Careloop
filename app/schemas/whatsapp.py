from datetime import datetime
from typing import Optional, List, Dict, Any, Literal

from pydantic import BaseModel, Field


# --- connection -------------------------------------------------------------

class WhatsAppStatusResponse(BaseModel):
    enabled: bool = False
    connected: bool = False
    status: Optional[str] = None
    display_phone_number: Optional[str] = None
    verified_name: Optional[str] = None
    quality_rating: Optional[str] = None
    waba_id: Optional[str] = None
    phone_number_id: Optional[str] = None
    templates_synced_at: Optional[datetime] = None
    opener_template_status: Optional[str] = None
    opener_preview: Optional[str] = None
    connected_at: Optional[datetime] = None
    last_error: Optional[str] = None

    model_config = {"from_attributes": True}


class ConnectRequest(BaseModel):
    code: str = Field(min_length=4, max_length=2000)
    waba_id: str = Field(min_length=1, max_length=64)
    phone_number_id: str = Field(min_length=1, max_length=64)


class ManualConnectRequest(BaseModel):
    access_token: str = Field(min_length=10, max_length=2000)
    waba_id: str = Field(min_length=1, max_length=64)
    phone_number_id: str = Field(min_length=1, max_length=64)


# --- templates --------------------------------------------------------------

class TemplateResponse(BaseModel):
    id: int
    name: str
    language: str
    category: Optional[str] = None
    status: Optional[str] = None
    parameter_format: str = "POSITIONAL"
    body_text: Optional[str] = None
    body_param_count: int = 0
    body_param_names: Optional[List[str]] = None
    header_type: Optional[str] = None
    supported: bool = True
    unsupported_reason: Optional[str] = None

    model_config = {"from_attributes": True}


# --- campaigns --------------------------------------------------------------

class TemplateParam(BaseModel):
    source: Literal["customer_name", "static"] = "static"
    value: Optional[str] = Field(default=None, max_length=1024)


class CampaignPreviewRequest(BaseModel):
    kind: Literal["template", "custom"]
    audience: Literal["all", "selected"] = "selected"
    customer_ids: Optional[List[int]] = Field(default=None, max_length=5000)


class CampaignPreviewResponse(BaseModel):
    total_selected: int
    eligible: int
    in_window: int
    needs_opener: int
    skipped: Dict[str, int]
    sample: List[Dict[str, Any]] = []


class CampaignCreateRequest(BaseModel):
    kind: Literal["template", "custom"]
    audience: Literal["all", "selected"] = "selected"
    customer_ids: Optional[List[int]] = Field(default=None, max_length=5000)
    name: Optional[str] = Field(default=None, max_length=255)
    # template mode
    template_name: Optional[str] = Field(default=None, max_length=512)
    template_language: Optional[str] = Field(default=None, max_length=16)
    params: Optional[List[TemplateParam]] = None
    # custom mode
    custom_body: Optional[str] = Field(default=None, max_length=4096)


class CampaignSummary(BaseModel):
    id: int
    name: Optional[str] = None
    kind: str
    audience: str
    status: str
    template_name: Optional[str] = None
    custom_body: Optional[str] = None
    total_selected: int = 0
    total_recipients: int = 0
    direct_count: int = 0
    opener_count: int = 0
    sent_count: int = 0
    delivered_count: int = 0
    read_count: int = 0
    replied_count: int = 0
    awaiting_count: int = 0
    failed_count: int = 0
    expired_count: int = 0
    skipped_count: int = 0
    skipped: Optional[Dict[str, int]] = None
    created_at: datetime
    completed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class CampaignListResponse(BaseModel):
    items: List[CampaignSummary]
    total: int


class CampaignMessageResponse(BaseModel):
    id: int
    customer_id: Optional[int] = None
    customer_name: Optional[str] = None
    to_phone: str
    kind: str
    status: str
    error_code: Optional[int] = None
    error_message: Optional[str] = None
    sent_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    read_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class CampaignDetailResponse(BaseModel):
    campaign: CampaignSummary
    messages: List[CampaignMessageResponse]
    messages_total: int


# --- single / test sends ----------------------------------------------------

class SingleSendRequest(BaseModel):
    customer_id: int
    text_body: str = Field(min_length=1, max_length=4096)


class SingleSendResponse(BaseModel):
    status: str
    wa_message_id: Optional[str] = None
    message: Optional[str] = None


class TestSendRequest(BaseModel):
    kind: Literal["template", "custom"] = "custom"
    to_phone: Optional[str] = Field(default=None, max_length=32)
    template_name: Optional[str] = Field(default=None, max_length=512)
    template_language: Optional[str] = Field(default=None, max_length=16)
    params: Optional[List[TemplateParam]] = None
    text_body: Optional[str] = Field(default=None, max_length=4096)


class SettingsUpdateRequest(BaseModel):
    """Reserved for future per-account settings."""
    pass
