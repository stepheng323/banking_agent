"""Read-only schedule responses while a confirmation interrupt is pending."""

from typing import Any

from apps.chat.src.agent.orchestrator.context.models import ContextFrameType
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.planning.task_planner import TaskPlanner
from apps.chat.src.agent.orchestrator.services.context_manager import OrchestratorContextManager
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.signals import (
    _could_be_schedule_interrupt_read_request,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_followup_surface_engine import (
    build_surface_answer_response as build_context_frame_followup_response,
)
from shared.types.planner import ContextFrameFollowupDecision


async def _resolve_schedule_read_during_pending_confirmation(
    *,
    state: OrchestratorState,
    interrupt: Any,
    text: str,
    task_planner: TaskPlanner | None,
    current_task_types: set[str],
) -> dict[str, Any] | None:
    """Answer read-only schedule asks without replaying a pending schedule confirmation."""
    if getattr(interrupt, "kind", None) != "confirmation":
        return None
    if "schedule" not in current_task_types:
        return None
    if task_planner is None:
        return None
    if not _could_be_schedule_interrupt_read_request(text):
        return None

    try:
        route = await task_planner.route_schedule_read_turn(
            state.phone_number,
            text,
            path_label="interrupt_path",
        )
    except Exception as exc:
        logger.warning("interrupt_schedule_read_router_failed", error=str(exc))
        return None

    decision = str(getattr(route, "decision", "") or "").strip()
    schedule_response_mode = getattr(route, "schedule_response_mode", None)
    confidence = float(getattr(route, "confidence", 0.0) or 0.0)
    if decision != "domain_schedule" or schedule_response_mode not in {"list", "count"} or confidence < 0.72:
        return None

    frame = OrchestratorContextManager().latest_active_frame(state)
    if frame is None or frame.frame_type != ContextFrameType.SCHEDULE_LIST:
        return None

    if schedule_response_mode == "count":
        count = len(frame.items)
        noun = "transaction" if count == 1 else "transactions"
        response = f"You have {count} pending scheduled {noun}."
        context_frames = state.context_frames
    else:
        frame_response = build_context_frame_followup_response(
            state,
            text,
            decision=ContextFrameFollowupDecision(
                decision="show_details",
                confidence=0.95,
                detected_language=getattr(route, "detected_language", None),
                reason="pending schedule confirmation read-only schedule request",
            ),
        )
        if frame_response is None or not frame_response.response:
            return None
        response = frame_response.response
        context_frames = frame_response.context_frames or state.context_frames

    logger.info(
        "interrupt_schedule_read_context_answer",
        schedule_response_mode=schedule_response_mode,
        confidence=round(confidence, 2),
        pending_task_ids=getattr(interrupt, "task_ids", None),
    )
    return {
        "pending_interrupt": interrupt,
        "last_interrupt": interrupt,
        "context_frames": context_frames,
        "outbox": [{"type": "say", "text": response}],
        "final_response": response,
        "semantic_path_shape": "interrupt_schedule_read_context",
        "routing_owner": "interrupt",
        "routing_decision": "domain_schedule",
        "routing_target_domain": "schedule",
        "route_source": "schedule_read_router",
    }


__all__ = ["_resolve_schedule_read_during_pending_confirmation"]
