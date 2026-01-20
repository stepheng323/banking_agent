"""V3 Transfer Data Model.

Strict Pydantic contract for the Transfer Subgraph.
"""

from typing import Any

from pydantic import BaseModel, Field


class TransferPayload(BaseModel):
    """Core business data for the transfer."""

    amount: float | None = None
    transfer_all: bool = False
    transfer_percentage: float | None = None

    recipient_name: str | None = None
    recipient_account: str | None = None
    recipient_bank_code: str | None = None
    recipient_bank_name: str | None = None
    recipient_resolved_name: str | None = None
    beneficiary_id: str | None = None

    source_account_id: str | None = None
    source_bank_name: str | None = None
    source_account_number: str | None = None

    # Payout / Funding (The complex plan)
    funding_plan: dict[str, Any] | None = None

    # Execution
    idempotency_key: str | None = None
    narration: str | None = None


class TransferContext(BaseModel):
    """Read-only context injected into pure nodes."""

    phone_number: str
    beneficiaries: list[dict[str, Any]] = Field(default_factory=list)
    accounts: list[dict[str, Any]] = Field(default_factory=list)


class TransferGates(BaseModel):
    """Security gates."""

    pin_verified: bool = False
    confirmation_confirmed: bool = False
