"""Read-only schedule responses while a confirmation interrupt is pending."""

from typing import Any

from apps.chat.src.agent.orchestrator.context.frame_manager import ContextFrameManager
from apps.chat.src.agent.orchestrator.context.models import ContextFrameType
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.gate.utils.semantic_router_llm import SemanticRouterLLM
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.signals import (
    _could_be_schedule_interrupt_read_request,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_detail_fields import frame_noun
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_followup_surface_engine import (
    build_surface_answer_response as build_context_frame_followup_response,
)
from banking.presentation.i18n.renderer import render_message
from shared.observability.llm import LLMCallDeadlineExceeded
from shared.types.planner import ContextFrameFollowupDecision
from shared.types.read import ReadRequest


async def _resolve_schedule_read_during_pending_confirmation(
    *,
    state: OrchestratorState,
    interrupt: Any,
    text: str,
    task_planner: SemanticRouterLLM | None,
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

    state_view = interrupt_state_view(state)
    try:
        route = await task_planner.route_schedule_read_turn(
            state_view.phone_number,
            text,
            path_label="interrupt_path",
        )
    except LLMCallDeadlineExceeded:
        # Let the interrupt node produce the localized timeout response while
        # preserving the pending confirmation as the active authority.
        raise
    except Exception as exc:
        logger.warning("interrupt_schedule_read_router_failed", error=str(exc))
        return None

    decision = str(getattr(route, "decision", "") or "").strip()
    read_request = getattr(route, "read_request", None)
    confidence = float(getattr(route, "confidence", 0.0) or 0.0)
    if (
        decision != "domain_schedule"
        or not isinstance(read_request, ReadRequest)
        or read_request.subject != "schedule"
        or confidence < 0.72
    ):
        return None

    frame = ContextFrameManager().latest_active_frame(state)
    if frame is None or frame.frame_type != ContextFrameType.SCHEDULE_LIST:
        return None
    loaded_context = state.loaded_context if isinstance(state.loaded_context, dict) else {}
    locale = str(loaded_context.get("language") or "en")

    if read_request.response_shape in {"fact_count", "fact_bool"}:
        count = len(frame.items)
        response = render_message(
            "context_frame.followup.pending_scheduled_count",
            locale,
            {
                "count": count,
                "noun": frame_noun(frame.frame_type, plural=count != 1, locale=locale),
            },
        )
        context_frames = state_view.context_frames
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
            locale=locale,
        )
        if frame_response is None or not frame_response.response:
            return None
        response = frame_response.response
        context_frames = frame_response.context_frames or state_view.context_frames

    logger.info(
        "interrupt_schedule_read_context_answer",
        response_shape=read_request.response_shape,
        confidence=round(confidence, 2),
        pending_task_ids=getattr(interrupt, "task_ids", None),
    )
    return {
        "pending_interrupt": interrupt,
        "last_interrupt": interrupt,
        "context_frames": context_frames,
        "outbox": [{"type": "say", "text": response}],
        "final_response": response,
        "path_shape": "interrupt_schedule_read_context",
    }


__all__ = ["_resolve_schedule_read_during_pending_confirmation"]
