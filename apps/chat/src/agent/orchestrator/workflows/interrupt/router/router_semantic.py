from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view
from apps.chat.src.agent.orchestrator.workflows.planner.context.rendering.context_rendering_router import (
    build_router_context_from_summary,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.summary.context_summary import (
    get_or_build_turn_context_summary,
)
from shared.types.planner import SemanticRouteDecision


async def _route_interrupt_semantic_turn(
    *,
    task_planner: Any,
    state: OrchestratorState,
    text: str,
    add_task_instruction_only: bool = False,
) -> SemanticRouteDecision | None:
    if task_planner is None:
        return None

    state_view = interrupt_state_view(state)
    if add_task_instruction_only:
        semantic_context = (
            "Pending confirmation add-task instruction. Classify only this fresh user instruction. "
            "Do not reuse active pending transfer recipients, banks, or task types unless they are explicitly "
            "mentioned in the instruction."
        )
    else:
        stashed_query_session = state_view.stashed_query_session
        summary, _ = get_or_build_turn_context_summary(
            state,
            query_session_snapshot=stashed_query_session,
            query_session_source="stashed" if stashed_query_session is not None else None,
            path_label="interrupt_path",
        )
        semantic_context = build_router_context_from_summary(
            summary,
            expected_executors=state_view.preplanner_expected_transaction_executors,
        )
    try:
        return cast(
            SemanticRouteDecision,
            await task_planner.route_semantic_turn(
                state_view.phone_number,
                text,
                context=semantic_context,
                path_label="interrupt_path",
            ),
        )
    except Exception as exc:
        logger.warning("interrupt_semantic_router_failed", error=str(exc))
        return None


__all__ = ["_route_interrupt_semantic_turn"]
