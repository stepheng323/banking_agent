"""Orchestrator Graph State Definition.

This state object is the single source of truth for the top-level OrchestratorGraph.
It manages user context, planning outputs, execution progress, and interrupt gating.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from apps.core.src.agent.workflow.models import TaskResult, TaskStatus
from shared.types.planner import PlannerOutput


class OrchestratorState(BaseModel):
    """
    Unified state for the Banking Agent Orchestrator.
    Persisted via LangGraph checkpointing.
    """

    # --- Identity & Session ---
    user_id: str
    phone_number: str

    # --- Input Context ---
    last_message_text: str | None = None
    last_message_id: str | None = None
    has_quote: bool = False
    quoted_message_id: str | None = None
    flow_callback: dict[str, Any] | None = None  # WhatsApp Flow submit payload

    # --- Planning ---
    normalized_instruction: str | None = None
    planner_output: PlannerOutput | None = None

    # --- Execution Tracking ---
    # Tasks are organized into waves (lists of task_ids) for topological execution
    waves: list[list[str]] = Field(default_factory=list)
    current_wave_index: int = 0
    
    # Task state and results
    task_status: dict[str, TaskStatus] = Field(default_factory=dict)
    task_results: dict[str, TaskResult] = Field(default_factory=dict)

    # --- Gating & Interrupts ---
    # Gates control flow progression
    pin_verified: bool = False
    
    # Interrupt Flags (drive the graph conditional edges)
    awaiting_auth: bool = False
    awaiting_input: bool = False
    
    # Context for interrupts
    pending_question: str | None = None
    pending_fields_by_task: dict[str, list[str]] = Field(default_factory=dict)

    # --- Policies ---
    max_parallel_transfers: int = 2
    max_parallel_queries: int = 5
    
    # --- Final Output ---
    final_response: str | None = None
