from typing import Any

from pydantic import BaseModel, Field

from apps.core.src.agent.graphs.data.models_extraction import DataExtractionResult


class DataPayload(BaseModel):
    """Payload for Data Graph execution."""

    idempotency_key: str | None = None
    extraction: DataExtractionResult | None = None
    amount: float | None = None
    network: str | None = None
    beneficiary_id: str | None = None
    target_phone: str | None = None
    plan_code: str | None = None
    plan_name: str | None = None
    is_self: bool = False
    stage: str = "init"
    confirmation: dict[str, Any] = Field(default_factory=dict)
    transaction_id: str | None = None
    receipt: dict[str, Any] | None = None
    error: str | None = None


class DataContext(BaseModel):
    """Context for Data Graph execution."""

    phone_number: str
    beneficiaries: list[dict[str, Any]] = Field(default_factory=list)
    accounts: list[dict[str, Any]] = Field(default_factory=list)
    user_id: str | None = None


class DataGates(BaseModel):
    """Gates for Data Graph execution."""

    pin_verified: bool = False
    confirmation_confirmed: bool = False
