"""V3 Transfer Data Model.

Strict Pydantic contract for the Transfer Subgraph.
"""

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field


class TransferGates(BaseModel):
    """Security gates."""

    pin_verified: bool = False
    confirmation_confirmed: bool = False


class TransferConfirmation(BaseModel):
    """Confirmation state within a transfer payload."""

    token: str | None = None
    summary: str | None = None
    snapshot_hash: str | None = None
    confirmed: bool = False


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
    recipient_reference: dict[str, Any] | None = None
    beneficiary_id: str | None = None
    beneficiary_candidates: list[dict[str, Any]] = Field(default_factory=list)
    is_self: bool = False
    resolved_from_saved_beneficiary: bool = False
    name_mismatch: bool = False
    name_match_score: float | None = None
    name_mismatch_warning: str | None = None

    source_account_id: str | None = None
    source_bank_name: str | None = None
    source_account_name: str | None = None
    source_account_number: str | None = None
    source_account_index: int | None = None
    source_affinity_mode: Literal["explicit", "auto"] = "auto"
    use_dual_accounts: bool | None = None
    source_accounts: list[str] | None = None
    explicit_split: dict[str, float] | None = None

    funding_plan: dict[str, Any] | None = None

    idempotency_key: str | None = None
    transaction_id: str | None = None
    narration: str | None = None
    description: str | None = None
    user_note: str | None = None
    suggested_amount: float | None = None
    is_high_risk_transfer: bool = False
    dynamic_risk_threshold: float | None = None
    high_risk_warning: str | None = None

    confirmation: TransferConfirmation = Field(default_factory=TransferConfirmation)
    skip_extraction: bool = False


class TransferContext(BaseModel):
    """Read-only context injected into pure nodes."""

    phone_number: str
    language: str = "en"
    beneficiaries: list[dict[str, Any]] = Field(default_factory=list)
    accounts: list[dict[str, Any]] = Field(default_factory=list)
    recent_beneficiary_context: bool = False


class TransferRecipient(TypedDict):
    """Details of the transfer recipient."""

    account_number: str
    bank_code: str
    name: str | None
    bank_name: str | None


class TransferSource(TypedDict):
    """Details of the funding source."""

    account_number: str | None
    bank_name: str | None
    account_name: str | None
    account_id: str | None


class FundingStepDict(TypedDict):
    """Details of a single funding step."""

    account_id: str
    amount: float
    bank_name: str
    sequence: int


class FundingPlanDict(TypedDict):
    """Details of the funding plan."""

    transfer_amount: float
    total_funded: float
    is_sufficient: bool
    is_single_source: bool
    steps: list[FundingStepDict]
    trigger_mode: Literal["auto", "explicit"]
    requested_sources: list[str]
    explicit_split_applied: bool
    planned_for_amount: float
    planned_for_source_account_id: str | None
    planned_for_source_accounts: list[str]
    planned_for_use_dual_accounts: bool
    planned_for_explicit_split: dict[str, float]


class TransferDataDict(TypedDict):
    """
    TypedDict for transfer data payload passed to executor.

    This matches the structure expected by the payment provider and logging.
    """

    amount: float
    recipient: TransferRecipient
    source: TransferSource
    narration: str | None
    funding_plan: FundingPlanDict | None


class TransferResultDict(TypedDict):
    """
    TypedDict for transfer execution result from provider.

    Standardized return format from all payment providers.
    """

    success: bool
    status: Literal["successful", "pending", "failed"]
    transaction_id: str | None
    reference: str | None
    amount: float | None
    fee: float | None
    currency: str | None
    provider_response: dict[str, Any] | None
    error: str | None
