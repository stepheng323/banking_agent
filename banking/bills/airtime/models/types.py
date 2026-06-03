"""Airtime Data Model.

Strict Pydantic contract for the Airtime worker pipeline.
"""

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field

from banking.runtime.results import TransactionOutcome, TransactionResult
from shared.money import MoneyAmount

AirtimeResult = TransactionResult
AirtimeOutcome = TransactionOutcome


class AirtimeGates(BaseModel):
    """Security gates."""

    pin_verified: bool = False
    confirmation_confirmed: bool = False


class AirtimeConfirmation(BaseModel):
    """Confirmation state within an airtime payload."""

    token: str | None = None
    summary: str | None = None
    snapshot_hash: str | None = None
    confirmed: bool = False


class AirtimePayload(BaseModel):
    """Core business data for the airtime purchase."""

    amount: MoneyAmount | None = None

    recipient_phone: str | None = None
    network: str | None = None

    recipient_name: str | None = None
    beneficiary_id: str | None = None
    referent_phone_candidates: list[dict[str, Any]] = Field(default_factory=list)
    is_self: bool = False

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

    idempotency_key: str | None = None
    transaction_id: str | None = None
    narration: str | None = None
    async_group_id: str | None = None
    async_group_size: int | None = None
    async_group_kind: Literal["single", "multi_transfer", "mixed_batch"] | None = None
    async_group_index: int | None = None

    correction_field: str | None = None
    correction_value: Any | None = None

    confirmation: AirtimeConfirmation = Field(default_factory=AirtimeConfirmation)
    previous_confirmation_snapshot: dict[str, Any] | None = None
    skip_extraction: bool = False


class AirtimeContext(BaseModel):
    """Read-only context injected into pure nodes."""

    phone_number: str
    language: str = "en"
    channel: str = "whatsapp"
    beneficiaries: list[dict[str, Any]] = Field(default_factory=list)
    accounts: list[dict[str, Any]] = Field(default_factory=list)
    all_accounts: list[dict[str, Any]] = Field(default_factory=list)
    referent_memory: dict[str, Any] = Field(default_factory=dict)
    resolved_referents: dict[str, Any] = Field(default_factory=dict)


class AirtimeRecipient(TypedDict):
    """Details of the airtime recipient."""

    phone_number: str
    network: str
    name: str | None


class AirtimeSource(TypedDict):
    """Details of the funding source."""

    account_number: str | None
    bank_name: str | None
    account_name: str | None
    account_id: str | None


class AirtimeDataDict(TypedDict):
    """
    TypedDict for airtime data payload passed to executor.
    Matches structure expected by provider adapters.
    """

    amount: MoneyAmount
    recipient: AirtimeRecipient
    source: AirtimeSource
    narration: str | None


class AirtimeResultDict(TypedDict):
    """
    TypedDict for airtime execution result from provider.
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
