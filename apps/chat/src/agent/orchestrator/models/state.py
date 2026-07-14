"""Orchestrator Graph State Definition.

This state object is the single source of truth for the top-level OrchestratorGraph.
It manages user context, planning outputs, execution progress, and interrupt gating.

CRITICAL: Orchestrator owns all durable state. Domain workers are ephemeral
and return structured results. Never let workers maintain competing state.
"""

from time import time
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from apps.chat.src.agent.orchestrator.context.models import ContextFrame
from apps.chat.src.agent.orchestrator.context.referents.models import ShortTermReferentMemory
from apps.chat.src.agent.orchestrator.models.domain import (
    ActiveSession,
    AuthorizationContext,
    PendingInterrupt,
    TaskSpec,
)
from apps.chat.src.agent.orchestrator.models.turn_directive import TurnDirective
from shared.types.planner import PlannerOutput
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class CapabilityBoundary(BaseModel):
    """Short-lived context for unsupported capability follow-ups."""

    key: str
    label: str
    followup_count: int = 0
    created_at_ts: float = Field(default_factory=time)
    last_updated_ts: float = Field(default_factory=time)
    ttl_seconds: int = 600


class OrchestratorState(BaseModel):
    """
    Unified state for the Banking Agent Orchestrator.
    Persisted via LangGraph checkpointing.

    This is the ONLY source of truth for workflow state.
    """

    schema_version: Literal["v2"] = "v2"

    @model_validator(mode="before")
    @classmethod
    def upgrade_legacy_checkpoint(cls, value: Any) -> Any:
        """Accept v1 checkpoints without reviving their competing route metadata."""
        if not isinstance(value, dict):
            return value
        legacy_route_keys = (
            "routing_owner",
            "routing_decision",
            "routing_target_domain",
            "routing_mode",
            "route_source",
            "routing_heuristic_type",
            "routing_heuristic_name",
        )
        version = value.get("schema_version")
        if version != "v1" and not (version is None and any(key in value for key in legacy_route_keys)):
            return value
        upgraded = dict(value)
        upgraded["schema_version"] = "v2"
        upgraded["turn_directive"] = None
        for key in legacy_route_keys:
            upgraded.pop(key, None)
        return upgraded

    @field_validator("turn_directive", mode="before", check_fields=False)
    @classmethod
    def drop_invalid_legacy_directive(cls, value: Any) -> Any:
        if value is None or isinstance(value, TurnDirective):
            return value
        try:
            return TurnDirective.model_validate(value)
        except ValidationError:
            logger.warning("invalid_turn_directive_dropped")
            return None

    user_id: str
    phone_number: str
    channel: str = "whatsapp"
    channel_identity: str | None = None

    last_message_text: str | None = None
    last_message_id: str | None = None
    last_activity_date: str | None = None
    last_callback: dict[str, Any] | None = None
    has_quote: bool = False
    quoted_message_id: str | None = None

    normalized_instruction: str | None = None
    planner_output: PlannerOutput | None = None

    @field_validator("planner_output", mode="before")
    @classmethod
    def drop_legacy_invalid_planner_output(cls, value: Any) -> Any:
        if value is None or isinstance(value, PlannerOutput):
            return value
        if isinstance(value, dict) and "primary_intent" not in value:
            logger.warning(
                "legacy_planner_output_dropped",
                reason="missing_primary_intent",
                keys=sorted(str(key) for key in value)[:12],
            )
            return None
        return value

    tasks: dict[str, TaskSpec] = Field(default_factory=dict)
    waves: list[list[str]] = Field(default_factory=list)
    current_wave_index: int = 0

    task_results: dict[str, Any] = Field(default_factory=dict)
    removed_confirmation_tasks: dict[str, dict[str, Any]] = Field(default_factory=dict)

    pin_verified: bool = False
    authorization_context: AuthorizationContext | None = None
    pending_interrupt: PendingInterrupt | None = None
    last_interrupt: PendingInterrupt | None = None
    outbox: list[dict[str, Any]] = Field(default_factory=list)
    final_response: str | None = None
    policy_notice: str | None = None
    preplanner_expected_transaction_executors: list[str] = Field(default_factory=list)

    # Context Frames (Upstream)
    context_frames: list[ContextFrame] = Field(default_factory=list)
    referent_memory: ShortTermReferentMemory = Field(default_factory=ShortTermReferentMemory)

    # Session stack and canonical route identity.
    session_stack: list[ActiveSession] = Field(default_factory=list)
    active_domain: str | None = None

    loaded_context: dict[str, Any] = Field(default_factory=dict)
    capability_boundary: CapabilityBoundary | None = None
    turn_context_summary: dict[str, Any] | None = None
    turn_directive: TurnDirective | None = None
    planner_used: bool = False
    planner_clean: bool | None = None
    planner_dirty_reasons: list[str] = Field(default_factory=list)
    suppress_empty_fallback: bool = False

    # Stashed Sessions (Upstream)
    stashed_sessions: list[dict[str, Any]] = Field(default_factory=list)
    pending_query_clarification: dict[str, Any] | None = None
    recent_query_context: dict[str, Any] | None = None
