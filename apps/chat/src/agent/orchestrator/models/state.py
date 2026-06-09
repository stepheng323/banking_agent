"""Orchestrator Graph State Definition (V3).

This state object is the single source of truth for the top-level OrchestratorGraph.
It manages user context, planning outputs, execution progress, and interrupt gating.

CRITICAL: Orchestrator owns all durable state. Domain workers are ephemeral
and return structured results. Never let workers maintain competing state.
"""

from time import time
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from apps.chat.src.agent.orchestrator.context.models import ContextFrame
from apps.chat.src.agent.orchestrator.context.referents.models import ShortTermReferentMemory
from apps.chat.src.agent.orchestrator.models.domain import ActiveSession, PendingInterrupt, TaskSpec
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
    Unified state for the Banking Agent Orchestrator (V3).
    Persisted via LangGraph checkpointing.

    This is the ONLY source of truth for workflow state.
    """

    schema_version: Literal["v1"] = "v1"

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
    pending_interrupt: PendingInterrupt | None = None
    last_interrupt: PendingInterrupt | None = None
    outbox: list[dict[str, Any]] = Field(default_factory=list)
    final_response: str | None = None
    policy_notice: str | None = None
    preplanner_expected_transaction_executors: list[str] = Field(default_factory=list)

    # Context Frames (Upstream)
    context_frames: list[ContextFrame] = Field(default_factory=list)
    referent_memory: ShortTermReferentMemory = Field(default_factory=ShortTermReferentMemory)

    # Direct Path & Session Stack (Optimization)
    direct_path_triggered: bool = False
    session_stack: list[ActiveSession] = Field(default_factory=list)
    active_domain: str | None = None

    loaded_context: dict[str, Any] = Field(default_factory=dict)
    capability_boundary: CapabilityBoundary | None = None
    turn_context_summary: dict[str, Any] | None = None
    semantic_path_shape: str | None = None
    routing_owner: str | None = None
    routing_decision: str | None = None
    routing_target_domain: str | None = None
    routing_mode: str | None = None
    route_source: str | None = None
    routing_heuristic_type: str | None = None
    routing_heuristic_name: str | None = None
    planner_used: bool = False
    planner_clean: bool | None = None
    planner_dirty_reasons: list[str] = Field(default_factory=list)
    suppress_empty_fallback: bool = False

    # Stashed Sessions (Upstream)
    stashed_sessions: list[dict[str, Any]] = Field(default_factory=list)
    stashed_query_session: dict[str, Any] | None = None
