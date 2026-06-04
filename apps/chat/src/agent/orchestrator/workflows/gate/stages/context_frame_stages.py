import re
from typing import Any

from apps.chat.src.agent.orchestrator.services.context_manager import OrchestratorContextManager
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.transaction_intents import (
    _classify_obvious_transfer_request,
    _is_obvious_airtime_request,
    _is_obvious_data_request,
    _obvious_mixed_transaction_executors,
)
from apps.chat.src.agent.orchestrator.workflows.gate.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.routing import (
    _route_observability_updates,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_followup_surface_engine import (
    build_surface_answer_context_for_state as build_context_frame_followup_context_for_state,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_followup_surface_engine import (
    build_surface_answer_response as build_context_frame_followup_response,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_followup_types import (
    ContextFrameFollowupResponse,
)
from banking.transactions.query.services.reasoning.shortcuts import resolve_query_shortcut
from shared.types.planner import ContextFrameFollowupDecision
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_CONTEXT_FRAME_REPLAY_CUE_RE = re.compile(
    r"\b(?:again|redo|repeat|replay|rerun|resend|same\s+again|send\s+again|"
    r"encore|repete|tun\s+se|tun|sake|maimaita|ziga)\b",
    re.IGNORECASE,
)
_CONTEXT_FRAME_DISPLAY_CUE_RE = re.compile(
    r"(?iu)(?:"
    r"\b(?:show|view|see|display|open|list|details?|more)\b|"
    r"\b(?:montre|voir|affiche|muestra|mostrar|ver)\b|"
    r"\b(?:fihan|wo|nuna|gani|gosi|lee)\b"
    r")"
)


def _is_fresh_transaction_command(ctx: GateContext) -> bool:
    if not ctx.phrase_heavy_fastpath_allowed:
        return False
    if _obvious_mixed_transaction_executors(ctx.message_text):
        return True
    if _classify_obvious_transfer_request(ctx.message_text) is not None:
        return True
    return _is_obvious_airtime_request(ctx.message_text) or _is_obvious_data_request(ctx.message_text)


def _looks_like_context_frame_replay(text: str) -> bool:
    return bool(_CONTEXT_FRAME_REPLAY_CUE_RE.search(text or ""))


def _looks_like_context_frame_display_request(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if not normalized or len(normalized) > 80:
        return False
    if re.search(r"\bmy\b", normalized, re.IGNORECASE):
        return False
    if re.search(r"(?:₦|ngn|\d)", normalized, re.IGNORECASE):
        return False
    return bool(_CONTEXT_FRAME_DISPLAY_CUE_RE.search(normalized))


def _looks_like_terse_context_frame_followup(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if not normalized:
        return False
    if len(normalized) > 80:
        return False
    tokens = re.findall(r"[\w']+", normalized, re.UNICODE)
    return len(tokens) <= 4


def _context_frame_followup_updates(
    ctx: GateContext,
    frame_followup: ContextFrameFollowupResponse,
) -> dict[str, Any]:
    return {
        **ctx.gate_updates,
        "direct_path_triggered": True,
        "semantic_path_shape": frame_followup.semantic_path_shape,
        "context_frames": frame_followup.context_frames or ctx.state_view.context_frames,
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


def _context_frame_followup_eligible(ctx: GateContext) -> bool:
    return (
        not ctx.live_pending_interrupt and not ctx.state_view.has_gate_blocking_state and ctx.task_planner is not None
    )


def _has_active_query_session(ctx: GateContext) -> bool:
    return bool(isinstance(ctx.query_session_snapshot, dict) and ctx.query_session_snapshot.get("session_active"))


def _display_shortcut_followup(ctx: GateContext) -> ContextFrameFollowupResponse | None:
    if not _looks_like_context_frame_display_request(ctx.message_text):
        return None
    return build_context_frame_followup_response(
        ctx.state,
        ctx.message_text,
        decision=ContextFrameFollowupDecision(
            decision="show_details",
            confidence=0.92,
            detected_language=ctx.current_locale,
            reason="short visible-context display request",
        ),
    )


async def _interpret_context_frame_followup(ctx: GateContext) -> ContextFrameFollowupDecision | None:
    if ctx.task_planner is None:
        return None
    try:
        return await ctx.task_planner.interpret_context_frame_followup(
            ctx.state_view.phone_number,
            ctx.message_text,
            context=build_context_frame_followup_context_for_state(ctx.state),
            path_label="direct_path",
        )
    except Exception as exc:
        logger.warning("gate_context_frame_followup_interpreter_failed", error=str(exc))
        return None


async def _extract_context_frame_replay_modifier(
    ctx: GateContext,
    decision: ContextFrameFollowupDecision,
) -> Any | None:
    if decision.decision not in {"replay_tasks", "replay"} or ctx.task_planner is None:
        return None
    try:
        replay_modifier = await ctx.task_planner.extract_context_frame_replay_modifiers(
            ctx.state_view.phone_number,
            ctx.message_text,
            context=build_context_frame_followup_context_for_state(ctx.state),
            path_label="direct_path",
        )
    except Exception as exc:
        logger.warning("gate_context_frame_replay_modifier_extractor_failed", error=str(exc))
        return None
    if replay_modifier is not None:
        logger.info(
            "gate_context_frame_replay_modifier_extracted",
            confidence=replay_modifier.confidence,
            detected_language=replay_modifier.detected_language,
            has_amount=replay_modifier.amount is not None,
            has_source=bool(replay_modifier.source_account_reference),
            has_narration=bool(replay_modifier.narration),
            reason=replay_modifier.reason,
        )
    return replay_modifier


async def _resolve_context_frame_followup(
    ctx: GateContext,
) -> tuple[ContextFrameFollowupDecision, ContextFrameFollowupResponse | None] | None:
    decision = await _interpret_context_frame_followup(ctx)
    if decision is None:
        return None
    replay_modifier = await _extract_context_frame_replay_modifier(ctx, decision)
    frame_followup = build_context_frame_followup_response(
        ctx.state,
        ctx.message_text,
        decision=decision,
        replay_modifier=replay_modifier,
    )
    return decision, frame_followup


async def _stage_context_frame_followup(ctx: GateContext) -> dict[str, Any] | None:
    """Resolve semantic follow-ups against the latest displayed response frame before domain routing."""
    if not _context_frame_followup_eligible(ctx):
        return None

    await ctx.ensure_query_session()
    if _has_active_query_session(ctx):
        if not _looks_like_context_frame_replay(ctx.message_text):
            logger.info("gate_context_frame_followup_skipped_for_active_query_session")
            return None
        logger.info("gate_context_frame_followup_active_query_replay_bypass")

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

    display_followup = _display_shortcut_followup(ctx)
    if display_followup:
        logger.info(
            "gate_context_frame_display_shortcut_hit",
            frame_type=frame.frame_type.value,
            item_count=len(frame.items),
        )
        return _context_frame_followup_updates(ctx, display_followup)

    resolved = await _resolve_context_frame_followup(ctx)
    if resolved is None:
        return None
    decision, frame_followup = resolved
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
    return _context_frame_followup_updates(ctx, frame_followup)


__all__ = ["_stage_context_frame_followup"]
