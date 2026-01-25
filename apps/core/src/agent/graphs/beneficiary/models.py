from enum import Enum

from pydantic import BaseModel, Field


class BeneficiaryIntent(str, Enum):
    """Intent for beneficiary operations."""

    LIST = "list_beneficiaries"
    ADD = "add_beneficiary"
    DELETE = "delete_beneficiary"
    UPDATE = "update_beneficiary"


class BeneficiaryPayload(BaseModel):
    """Payload for beneficiary task."""

    intent: BeneficiaryIntent | None = None

    # For Add/Update
    name: str | None = Field(default=None, description="Name of person/entity")
    alias: str | None = Field(default=None, description="User-friendly alias (e.g., 'Mum')")
    account_number: str | None = Field(default=None)
    bank_name: str | None = Field(default=None)
    bank_code: str | None = Field(default=None)

    # For Delete/Update
    target_alias: str | None = Field(default=None, description="Alias/Name to identify beneficiary to modify")

    # Context
    user_id: str | None = None
    phone_number: str | None = None
