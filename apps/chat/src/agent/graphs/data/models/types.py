from typing import Any, Literal

from pydantic import BaseModel, Field

from apps.chat.src.agent.graphs.data.models_extraction import DataExtractionResult


class DataPayload(BaseModel):
    """Payload for Data Graph execution."""

    idempotency_key: str | None = None
    extraction: DataExtractionResult | None = None
    amount: float | None = None
    network: str | None = None
    beneficiary_id: str | None = None
    recipient_name: str | None = None
    referent_phone_candidates: list[dict[str, Any]] = Field(default_factory=list)
    target_phone: str | None = None
    plan_code: str | None = None
    plan_name: str | None = None
    biller_code: str | None = None
    plan_size_gb: float | None = None
    plan_validity_days: int | None = None
    plan_tags: list[str] = Field(default_factory=list)
    size_preference: str | None = None
    validity_preference: str | None = None
    selection_preference: str | None = None
    usage_intent: str | None = None
    data_plan_candidates: list[dict[str, Any]] = Field(default_factory=list)
    show_plan_options: bool = False
    data_plan_exclude_codes: list[str] = Field(default_factory=list)
    catalog_cache_stale: bool = False
    is_self: bool = False
    stage: str = "init"
    confirmation: dict[str, Any] = Field(default_factory=dict)
    transaction_id: str | None = None
    receipt: dict[str, Any] | None = None
    error: str | None = None
    skip_extraction: bool = False
    previous_confirmation_snapshot: dict[str, Any] | None = None
    async_group_id: str | None = None
    async_group_size: int | None = None
    async_group_kind: Literal["single", "multi_transfer", "mixed_batch"] | None = None
    async_group_index: int | None = None
    source_account_id: str | None = None
    source_bank_name: str | None = None
    source_account_name: str | None = None
    source_account_number: str | None = None
    source_account_index: int | None = None
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


class DataContext(BaseModel):
    """Context for Data Graph execution."""

    phone_number: str
    language: str = "en"
    channel: str = "whatsapp"
    beneficiaries: list[dict[str, Any]] = Field(default_factory=list)
    accounts: list[dict[str, Any]] = Field(default_factory=list)
    all_accounts: list[dict[str, Any]] = Field(default_factory=list)
    referent_memory: dict[str, Any] = Field(default_factory=dict)
    resolved_referents: dict[str, Any] = Field(default_factory=dict)
    user_id: str | None = None


class DataGates(BaseModel):
    """Gates for Data Graph execution."""

    pin_verified: bool = False
    confirmation_confirmed: bool = False
