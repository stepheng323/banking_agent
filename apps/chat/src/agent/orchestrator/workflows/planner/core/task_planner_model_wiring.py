"""Structured-output model wiring for TaskPlanner."""

from dataclasses import dataclass
from typing import Any, Literal

from langchain_openai import ChatOpenAI

from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_models import (
    UnsupportedBoundaryTurnOutput,
    UnsupportedCapabilitySemanticOutput,
)
from banking.transactions.shared.confirmation.models import ConfirmationDecisionOutput
from shared.types.planner import (
    ContextFrameFollowupDecision,
    ContextFrameReplayModifier,
    InterruptRouteDecision,
    PendingActionEditDecision,
    PlannerOutput,
    SemanticRouteDecision,
)
from shared.types.quoted_replay import QuotedReplayInterpretation


@dataclass(frozen=True)
class TaskPlannerStructuredOutputs:
    planner: Any
    semantic_router: Any
    schedule_read_router: Any
    interrupt_router: Any
    quoted_replay: Any
    context_frame_followup: Any
    context_frame_replay_modifier: Any
    pending_action_edit: Any
    confirmation_decision: Any
    unsupported_capability: Any
    unsupported_boundary_turn: Any


def with_structured_output(
    llm: ChatOpenAI,
    schema: type[Any],
    *,
    method: Literal["function_calling", "json_mode", "json_schema"] | None = None,
) -> Any:
    kwargs: dict[str, Any] = {"include_raw": True}
    if method is None:
        try:
            return llm.with_structured_output(schema, **kwargs)
        except TypeError as exc:
            if not _is_unsupported_include_raw_error(str(exc)):
                raise
            return llm.with_structured_output(schema)
    try:
        return llm.with_structured_output(schema, method=method, **kwargs)
    except TypeError as exc:
        if not _is_unsupported_include_raw_error(str(exc)):
            raise
        return llm.with_structured_output(schema, method=method)


def _is_unsupported_include_raw_error(message: str) -> bool:
    return "include_raw" in message and (
        "unexpected keyword" in message or "got an unexpected keyword argument" in message
    )


def build_task_planner_structured_outputs(
    *,
    planner_llm: ChatOpenAI,
    semantic_router_llm: ChatOpenAI,
    interrupt_llm: ChatOpenAI,
) -> TaskPlannerStructuredOutputs:
    return TaskPlannerStructuredOutputs(
        planner=with_structured_output(
            planner_llm,
            PlannerOutput,
            method="function_calling",
        ),
        semantic_router=with_structured_output(
            semantic_router_llm,
            SemanticRouteDecision,
        ),
        schedule_read_router=with_structured_output(
            semantic_router_llm,
            SemanticRouteDecision,
        ),
        interrupt_router=with_structured_output(
            interrupt_llm,
            InterruptRouteDecision,
        ),
        quoted_replay=with_structured_output(
            planner_llm,
            QuotedReplayInterpretation,
        ),
        context_frame_followup=with_structured_output(
            semantic_router_llm,
            ContextFrameFollowupDecision,
        ),
        context_frame_replay_modifier=with_structured_output(
            semantic_router_llm,
            ContextFrameReplayModifier,
        ),
        pending_action_edit=with_structured_output(
            interrupt_llm,
            PendingActionEditDecision,
        ),
        confirmation_decision=with_structured_output(
            interrupt_llm,
            ConfirmationDecisionOutput,
        ),
        unsupported_capability=with_structured_output(
            semantic_router_llm,
            UnsupportedCapabilitySemanticOutput,
        ),
        unsupported_boundary_turn=with_structured_output(
            semantic_router_llm,
            UnsupportedBoundaryTurnOutput,
        ),
    )
