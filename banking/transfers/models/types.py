"""V3 Transfer Data Model.

Strict Pydantic contract for the Transfer worker pipeline.
"""

from typing import Any, Literal, NotRequired, TypedDict

from pydantic import BaseModel, Field, field_validator

from shared.money import MoneyAmount


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

    amount: MoneyAmount | None = None
    transfer_all: bool = False
    transfer_percentage: float | None = None

    recipient_name: str | None = None
    recipient_account: str | None = None
    recipient_bank_code: str | None = None
    recipient_bank_name: str | None = None
    recipient_bank_code_provider: str | None = None
    recipient_resolution_provider: str | None = None
    recipient_resolution_mode: Literal["single_source", "pooled"] | None = None
    recipient_resolved_name: str | None = None
    recipient_reference: dict[str, Any] | None = None
    recipient_binding_source: Literal["fanout"] | None = None
    recipient_binding_index: int | None = None
    beneficiary_id: str | None = None
    beneficiary_candidates: list[dict[str, Any]] = Field(default_factory=list)
    referent_recipient_candidates: list[dict[str, Any]] = Field(default_factory=list)
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
    explicit_split: dict[str, MoneyAmount] | None = None

    funding_plan: dict[str, Any] | None = None
    suggested_funding_plan: dict[str, Any] | None = None

    idempotency_key: str | None = None
    transaction_id: str | None = None
    authored_narration: str | None = None
    narration: str | None = None
    async_group_id: str | None = None
    async_group_size: int | None = None
    async_group_kind: Literal["single", "multi_transfer", "mixed_batch"] | None = None
    async_group_index: int | None = None
    description: str | None = None
    user_note: str | None = None
    transition_acknowledgment: str | None = None
    previous_confirmation_snapshot: dict[str, Any] | None = None
    suggested_amount: MoneyAmount | None = None
    amount_suggestion_disabled: bool = False
    is_high_risk_transfer: bool = False
    dynamic_risk_threshold: float | None = None
    high_risk_warning: str | None = None
    risk_advisory_reason_codes: list[str] = Field(default_factory=list)
    risk_advisory_score: int = 0

    confirmation: TransferConfirmation = Field(default_factory=TransferConfirmation)
    skip_extraction: bool = False
    confirmation_message_scoped: bool = False

    # Scheduling fields (phase 1 transfer scheduling)
    schedule_mode: Literal["one_time", "recurring"] | None = None
    recurrence_type: Literal["one_time", "daily", "weekly", "monthly"] | None = None
    schedule_timezone: str | None = None
    schedule_start_date: str | None = None
    schedule_time_local: str | None = None
    schedule_day_of_week: int | None = None
    schedule_day_of_month: int | None = None
    schedule_end_date: str | None = None
    schedule_id: str | None = None
    schedule_selector: str | None = None
    schedule_operation_note: str | None = None
    schedule_response_mode: Literal["list", "count"] | None = None
    schedule_edit_patch: dict[str, Any] | None = None
    schedule_edit_requires_auth: bool | None = None
    schedule_edit_next_run_at_utc: str | None = None

    # Generic schedule-management edit fields for airtime/data schedules.
    recipient_phone: str | None = None
    network: str | None = None
    target_phone: str | None = None
    plan_code: str | None = None
    plan_name: str | None = None

    @field_validator("transfer_all", mode="before")
    @classmethod
    def normalize_transfer_all(cls, value: Any) -> bool:
        if value is None:
            return False
        return bool(value)

    @field_validator("source_affinity_mode", mode="before")
    @classmethod
    def normalize_source_affinity_mode(cls, value: Any) -> str:
        if value is None:
            return "auto"
        return value

    @field_validator("confirmation", mode="before")
    @classmethod
    def normalize_confirmation(cls, value: Any) -> TransferConfirmation:
        if isinstance(value, TransferConfirmation):
            return value
        if isinstance(value, dict):
            return TransferConfirmation(**value)
        if value is None:
            return TransferConfirmation()
        return value


class TransferContext(BaseModel):
    """Read-only context injected into pure nodes."""

    phone_number: str
    language: str = "en"
    beneficiaries: list[dict[str, Any]] = Field(default_factory=list)
    accounts: list[dict[str, Any]] = Field(default_factory=list)
    all_accounts: list[dict[str, Any]] = Field(default_factory=list)
    referent_memory: dict[str, Any] = Field(default_factory=dict)
    resolved_referents: dict[str, Any] = Field(default_factory=dict)
    channel: str = "whatsapp"
    channel_identity: str | None = None


class TransferRecipient(TypedDict):
    """Details of the transfer recipient."""

    account_number: str
    bank_code: str
    name: str | None
    bank_name: str | None
    bank_code_provider: NotRequired[str | None]
    resolution_provider: NotRequired[str | None]


class TransferSource(TypedDict):
    """Details of the funding source."""

    account_number: str | None
    bank_name: str | None
    account_name: str | None
    account_id: str | None


class FundingStepDict(TypedDict):
    """Details of a single funding step."""

    account_id: str
    amount: MoneyAmount
    bank_name: str
    sequence: int


class FundingPlanDict(TypedDict):
    """Details of the funding plan."""

    transfer_amount: MoneyAmount
    total_funded: float
    is_sufficient: bool
    is_single_source: bool
    steps: list[FundingStepDict]
    trigger_mode: Literal["auto", "explicit"]
    requested_sources: list[str]
    explicit_split_applied: bool
    planned_for_amount: MoneyAmount
    planned_for_source_account_id: str | None
    planned_for_source_accounts: list[str]
    planned_for_use_dual_accounts: bool
    planned_for_explicit_split: dict[str, MoneyAmount]


class TransferDataDict(TypedDict):
    """
    TypedDict for transfer data payload passed to executor.

    This matches the structure expected by the payment provider and logging.
    """

    amount: MoneyAmount
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
    amount: MoneyAmount | None
    fee: MoneyAmount | None
    currency: str | None
    provider_response: dict[str, Any] | None
    error: str | None
