"""Account models for data validation and serialization."""
from typing import Any, Dict, Optional, Literal

from pydantic import BaseModel, Field


MandateStatus = Literal["pending", "approved", "ready", "rejected", "cancelled"]


class CreateAccount(BaseModel):
    """Model for creating a bank account."""

    user_id: str
    account_id: str
    bank_name: str
    bank_code: Optional[str] = None
    account_number: str
    account_name: Optional[str] = None
    is_default: bool = False
    mandate_id: Optional[str] = None
    mandate_status: MandateStatus = "pending"
    extra_data: Dict[str, Any] = Field(default_factory=dict)


class AccountUpdate(BaseModel):
    """Model for updating a bank account."""
    mandate_id: Optional[str] = None
    mandate_status: Optional[MandateStatus] = None
    is_default: Optional[bool] = None


class Account(BaseModel):
    """Model for a bank account."""

    id: str
    user_id: str
    account_id: str
    bank_name: str
    bank_code: Optional[str] = None
    account_number: str
    account_name: Optional[str] = None
    mandate_id: Optional[str] = None
    mandate_status: MandateStatus = "pending"
    extra_data: Dict[str, Any] = Field(default_factory=dict)