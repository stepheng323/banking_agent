"""Account models for data validation and serialization."""

from typing import Any, Literal

from pydantic import BaseModel, Field

MandateStatus = Literal["pending", "approved", "ready", "rejected", "cancelled"]


class CreateAccount(BaseModel):
    """Model for creating a bank account."""

    user_id: str
    account_id: str
    bank_name: str
    bank_code: str | None = None
    account_number: str
    account_name: str | None = None
    is_default: bool = False
    mandate_id: str | None = None
    mandate_status: MandateStatus = "pending"
    extra_data: dict[str, Any] = Field(default_factory=dict)


class AccountUpdate(BaseModel):
    """Model for updating a bank account."""

    mandate_id: str | None = None
    mandate_status: MandateStatus | None = None
    is_default: bool | None = None


class Account(BaseModel):
    """Model for a bank account."""

    id: str
    user_id: str
    account_id: str
    bank_name: str
    bank_code: str | None = None
    account_number: str
    account_name: str | None = None
    mandate_id: str | None = None
    mandate_status: MandateStatus = "pending"
    extra_data: dict[str, Any] = Field(default_factory=dict)
