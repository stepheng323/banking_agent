from typing import Any

from apps.chat.src.agent.graphs.query.services.query_shortcuts import resolve_query_shortcut
from apps.chat.src.agent.orchestrator.nodes.gate.pipeline.context import GateContext
from apps.chat.src.agent.orchestrator.nodes.gate.runner import (
    _classify_obvious_transfer_request,
    _is_obvious_airtime_request,
    _is_obvious_data_request,
    _obvious_mixed_transaction_executors,
    _route_observability_updates,
)
from apps.chat.src.agent.orchestrator.nodes.planner.context_frame_followup import (
    build_context_frame_followup_context_for_state,
    build_context_frame_followup_response,
)
from apps.chat.src.agent.orchestrator.services.context_manager import OrchestratorContextManager
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _is_fresh_transaction_command(ctx: GateContext) -> bool:
    if not ctx.phrase_heavy_fastpath_allowed:
        return False
    if _obvious_mixed_transaction_executors(ctx.message_text):
        return True
    if _classify_obvious_transfer_request(ctx.message_text) is not None:
        return True
    return _is_obvious_airtime_request(ctx.message_text) or _is_obvious_data_request(ctx.message_text)


async def _stage_context_frame_followup(ctx: GateContext) -> dict[str, Any] | None:
    """Resolve semantic follow-ups against the latest displayed response frame before domain routing."""
    if (
        ctx.live_pending_interrupt
        or ctx.state.pending_interrupt is not None
        or ctx.state.has_quote
        or ctx.state.session_stack
        or ctx.state.waves
        or not callable(getattr(ctx.task_planner, "interpret_context_frame_followup", None))
    ):
        return None

    await ctx.ensure_query_session()
    if isinstance(ctx.query_session_snapshot, dict) and ctx.query_session_snapshot.get("session_active"):
        logger.info("gate_context_frame_followup_skipped_for_active_query_session")
        return None

    frame = OrchestratorContextManager().latest_active_frame(ctx.state)
    if frame is None or not frame.items:
        return None
    shortcut = resolve_query_shortcut(ctx.message_text, ctx.current_locale)
    if shortcut is not None and shortcut.kind == "pagination":
        logger.info(
            "gate_context_frame_followup_skipped_for_query_pagination",
            action=shortcut.action,
            frame_type=frame.frame_type.value,
            item_count=len(frame.items),
        )
        return None
    if _is_fresh_transaction_command(ctx):
        logger.info(
            "gate_context_frame_followup_skipped_for_fresh_transaction",
            frame_type=frame.frame_type.value,
            item_count=len(frame.items),
        )
        return None

    try:
        decision = await ctx.task_planner.interpret_context_frame_followup(
            ctx.state.phone_number,
            ctx.message_text,
            context=build_context_frame_followup_context_for_state(ctx.state),
            path_label="direct_path",
        )
    except Exception as exc:
        logger.warning("gate_context_frame_followup_interpreter_failed", error=str(exc))
        return None

    frame_followup = build_context_frame_followup_response(ctx.state, ctx.message_text, decision=decision)
    logger.info(
        "gate_context_frame_followup_decision",
        decision=decision.decision,
        confidence=decision.confidence,
        detected_language=decision.detected_language,
        requested_field=decision.requested_field,
        rank=decision.rank,
        has_filters=bool(decision.filters),
        reason=decision.reason,
        resolved=bool(frame_followup),
        frame_type=frame.frame_type.value,
        item_count=len(frame.items),
    )
    if not frame_followup:
        return None

    logger.info(
        "gate_context_frame_followup_hit",
        frame_type=frame.frame_type.value,
        item_count=len(frame.items),
    )
    return {
        **ctx.gate_updates,
        "direct_path_triggered": True,
        "semantic_path_shape": frame_followup.semantic_path_shape,
        "context_frames": frame_followup.context_frames or ctx.state.context_frames,
        **({"final_response": frame_followup.response} if frame_followup.response else {}),
        **({"tasks": frame_followup.tasks} if frame_followup.tasks else {}),
        **({"waves": frame_followup.waves} if frame_followup.waves else {}),
        **({"current_wave_index": 0} if frame_followup.waves else {}),
        **_route_observability_updates(
            owner="guardrail",
            decision="context_frame_followup",
            target_domain=frame_followup.recent_domain_focus,
        ),
    }


__all__ = ["_stage_context_frame_followup"]
