"""Orchestrator Graph State Definition (V3).

This state object is the single source of truth for the top-level OrchestratorGraph.
It manages user context, planning outputs, execution progress, and interrupt gating.

CRITICAL: Orchestrator owns all durable state. Subgraphs are ephemeral workers
that return structured results. Never let subgraphs maintain competing state.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from apps.core.src.agent.orchestrator.context.models import ContextFrame
from apps.core.src.agent.orchestrator.models.domain import ActiveSession, PendingInterrupt, TaskSpec
from shared.types.planner import PlannerOutput


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
    last_callback: dict[str, Any] | None = None
    has_quote: bool = False
    quoted_message_id: str | None = None

    normalized_instruction: str | None = None
    planner_output: PlannerOutput | None = None

    tasks: dict[str, TaskSpec] = Field(default_factory=dict)
    waves: list[list[str]] = Field(default_factory=list)
    current_wave_index: int = 0

    task_results: dict[str, Any] = Field(default_factory=dict)

    pin_verified: bool = False
    pending_interrupt: PendingInterrupt | None = None
    last_interrupt: PendingInterrupt | None = None
    outbox: list[dict[str, Any]] = Field(default_factory=list)
    final_response: str | None = None
    policy_notice: str | None = None

    # Context Frames (Upstream)
    context_frames: list[ContextFrame] = Field(default_factory=list)

    # Fast Path & Session Stack (Optimization)
    fast_path_triggered: bool = False
    session_stack: list[ActiveSession] = Field(default_factory=list)
    active_domain: str | None = None

    loaded_context: dict[str, Any] = Field(default_factory=dict)

    # Stashed Sessions (Upstream)
    stashed_sessions: list[dict[str, Any]] = Field(default_factory=list)
    stashed_query_session: dict[str, Any] | None = None
