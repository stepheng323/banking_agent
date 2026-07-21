"""Structured models for quoted replay interpretation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

QuotedReplayDecision = Literal["not_replay", "execute", "clarify"]
ReplayFailureCategory = Literal[
    "provider_unavailable",
    "provider_declined",
    "insufficient_funds",
    "validation_error",
    "source_account",
    "execution_error",
    "unknown",
]
ReplayFinalStatus = Literal["success", "processing", "failed"]
ReplayTargetType = Literal["transfer", "airtime", "data"]


class _StrictModel(BaseModel):
    """Base strict model for OpenAI structured-output schemas."""

    model_config = ConfigDict(extra="forbid")


class ReplayTaskConfirmation(_StrictModel):
    """Confirmation reset metadata carried by replay task payloads."""

    confirmed: bool | None = None


class ReplayTaskPayload(_StrictModel):
    """Typed replay task payload (no free-form additional properties)."""

    action: Literal["send_money", "buy_airtime", "buy_data"] | None = None
    amount: float | None = None
    beneficiary_id: str | None = None
    recipient_name: str | None = None
    recipient_resolved_name: str | None = None
    recipient_phone: str | None = None
    target_phone: str | None = None
    recipient_account: str | None = None
    recipient_account_number: str | None = None
    recipient_bank_code: str | None = None
    recipient_bank_name: str | None = None
    source_bank_name: str | None = None
    resolved_from_saved_beneficiary: bool | None = None
    source_account_id: str | None = None
    source_account_number: str | None = None
    source_account_index: int | None = None
    source_affinity_mode: Literal["explicit", "auto"] | None = None
    narration: str | None = None
    network: str | None = None
    plan_code: str | None = None
    plan_name: str | None = None
    instruction: str | None = None
    message: str | None = None
    idempotency_key: str | None = None
    transaction_id: str | None = None
    skip_extraction: bool | None = None
    confirmation: ReplayTaskConfirmation | None = None
    final_status: ReplayFinalStatus | None = None
    error_message: str | None = None
    failure_category: ReplayFailureCategory | None = None


class ReplayExecutableTask(_StrictModel):
    """Executable task returned by quoted replay interpreter."""

    task_type: ReplayTargetType
    payload: ReplayTaskPayload = Field(default_factory=ReplayTaskPayload)


class QuotedReplayInterpretation(_StrictModel):
    """Structured parser output for quoted replay turns."""

    decision: QuotedReplayDecision = "not_replay"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    detected_language: str | None = None
    tasks: list[ReplayExecutableTask] = Field(default_factory=list)
    target_statuses: list[ReplayFinalStatus] = Field(default_factory=list)
    target_types: list[ReplayTargetType] = Field(default_factory=list)
    target_task_ids: list[str] = Field(default_factory=list)
    clarify_message: str | None = None
    replay_amount: float | None = Field(default=None, gt=0)
    replay_amount_evidence: str | None = Field(default=None, max_length=120)
    replay_source_account: str | None = Field(default=None, max_length=120)
    replay_source_evidence: str | None = Field(default=None, max_length=120)
    replay_narration: str | None = Field(default=None, max_length=240)
    replay_narration_evidence: str | None = Field(default=None, max_length=240)
    reason: str | None = None
