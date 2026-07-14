import re
from typing import Any

from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_detection import (
    detect_unsupported_capability,
)
from apps.chat.src.agent.orchestrator.context.frame_manager import ContextFrameManager
from apps.chat.src.agent.orchestrator.context.models import ContextFrame, ContextFrameType
from apps.chat.src.agent.orchestrator.conversation.conversation_responder_text import (
    is_contextual_casual_followup_turn,
)
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.turn_directive import RouteResolution
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.casual import (
    looks_like_obvious_casual_or_meta_turn,
)
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.direct_domains import (
    _is_account_balance_request,
    _is_account_domain_request,
    _is_beneficiary_domain_request,
    _is_query_domain_request,
)
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.transaction_intents import (
    _classify_obvious_transfer_request,
    _is_obvious_airtime_request,
    _is_obvious_data_request,
    _obvious_mixed_transaction_executors,
)
from apps.chat.src.agent.orchestrator.workflows.gate.core.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.core.outcomes import direct_response, task_dispatch
from apps.chat.src.agent.orchestrator.workflows.gate.utils.direct_tasks import _next_direct_domain_task_id
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_followup_surface_engine import (
    build_surface_answer_context_for_state as build_context_frame_followup_context_for_state,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_followup_surface_engine import (
    build_surface_answer_response as build_context_frame_followup_response,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_followup_types import (
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
    r"\b(?:show|view|see|display|open|list|details?|more|fetch)\b|"
    r"\b(?:montre|voir|affiche|muestra|mostrar|ver)\b|"
    r"\b(?:fihan|wo|nuna|gani|gosi|lee)\b"
    r")"
)
_READ_ONLY_REFRESH_CUE_RE = re.compile(
    r"(?iu)^(?:"
    r"(?:fetch|refresh|reload|recheck)(?:\s+(?:it|this|them|those|again|againo|same|list|result|results))*|"
    r"check(?:\s+(?:it|this|them|those|same))?\s+againo?|"
    r"(?:show|display|list|view)\s+(?:it|this|them|those|same|list|result|results)(?:\s+againo?)?|"
    r"(?:run|try)\s+(?:it|this|them|those|same)\s+againo?"
    r")$"
)
_MONEY_MOVE_REFRESH_BLOCK_RE = re.compile(
    r"(?iu)\b(?:send|transfer|pay|buy|airtime|data|bundle|recharge|top\s*up|do|redo|resend)\b"
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


def _looks_like_read_only_refresh_request(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if not normalized or len(normalized) > 80:
        return False
    if re.search(r"(?:₦|ngn|\d)", normalized, re.IGNORECASE):
        return False
    if _MONEY_MOVE_REFRESH_BLOCK_RE.search(normalized):
        return False
    return bool(_READ_ONLY_REFRESH_CUE_RE.search(normalized))


def _context_frame_followup_updates(
    ctx: GateContext,
    frame_followup: ContextFrameFollowupResponse,
) -> RouteResolution:
    extra_updates: dict[str, Any] = {
        "context_frames": frame_followup.context_frames or ctx.state_view.context_frames,
    }
    if frame_followup.response:
        extra_updates["final_response"] = frame_followup.response
    if frame_followup.tasks and frame_followup.waves:
        return task_dispatch(
            ctx,
            tasks=frame_followup.tasks,
            waves=frame_followup.waves,
            owner="semantic_router",
            decision="context_frame_followup",
            path_shape=frame_followup.path_shape,
            extra_updates=extra_updates,
            target_domain=frame_followup.recent_domain_focus,
            mode="continuation",
            source="context_frame_followup",
        )
    return direct_response(
        ctx,
        response=frame_followup.response or "",
        owner="semantic_router",
        decision="context_frame_followup",
        path_shape=frame_followup.path_shape,
        extra_updates=extra_updates,
        target_domain=frame_followup.recent_domain_focus,
        mode="continuation",
        source="context_frame_followup",
    )


def _build_read_only_refresh_spec(ctx: GateContext, frame: ContextFrame | None) -> tuple[str, TaskSpec, str] | None:
    if frame is not None:
        if frame.frame_type == ContextFrameType.ACCOUNT_LIST:
            source_action = str(frame.metadata.get("source_action") or "").strip()
            action = (
                "check_balance"
                if source_action in {"check_balance", "balance", "show_balance", "overall_balance"}
                else "list_accounts"
            )
            task_id = _next_direct_domain_task_id(ctx.state_view.tasks, "account")
            account_payload: dict[str, Any] = {
                "action": action,
                "message": ctx.message_text,
                "instruction": ctx.message_text,
            }
            if action == "list_accounts":
                account_payload["response_shape"] = "surface_list"
            return task_id, TaskSpec(id=task_id, type="account", stage=TaskStage.DRAFT, payload=account_payload), action

        if frame.frame_type == ContextFrameType.BENEFICIARY_LIST:
            task_id = _next_direct_domain_task_id(ctx.state_view.tasks, "beneficiary")
            beneficiary_payload: dict[str, Any] = {
                "action": "list_beneficiaries",
                "intent": "list_beneficiaries",
                "list_intent": True,
                "message": ctx.message_text,
                "instruction": ctx.message_text,
                "response_shape": "surface_list",
            }
            return (
                task_id,
                TaskSpec(id=task_id, type="beneficiary", stage=TaskStage.DRAFT, payload=beneficiary_payload),
                "list_beneficiaries",
            )

        if frame.frame_type == ContextFrameType.SCHEDULE_LIST:
            task_id = _next_direct_domain_task_id(ctx.state_view.tasks, "schedule")
            schedule_payload: dict[str, Any] = {
                "action": "list_scheduled_transactions",
                "message": ctx.message_text,
                "instruction": ctx.message_text,
                "response_shape": "surface_list",
                "schedule_response_mode": "list",
            }
            return (
                task_id,
                TaskSpec(id=task_id, type="schedule", stage=TaskStage.DRAFT, payload=schedule_payload),
                "list_scheduled_transactions",
            )

        if frame.frame_type == ContextFrameType.TRANSACTION_LIST and _is_query_surface_frame(frame):
            task_id = _next_direct_domain_task_id(ctx.state_view.tasks, "query")
            payload = {
                "message": ctx.message_text,
                "instruction": ctx.message_text,
            }
            return task_id, TaskSpec(id=task_id, type="query", stage=TaskStage.DRAFT, payload=payload), "repeat_query"

    latest_account_task = _latest_completed_read_only_account_task(ctx)
    if latest_account_task is not None:
        action = str(latest_account_task.payload.get("action") or "").strip()
        task_id = _next_direct_domain_task_id(ctx.state_view.tasks, "account")
        return (
            task_id,
            TaskSpec(
                id=task_id,
                type="account",
                stage=TaskStage.DRAFT,
                payload={"action": action, "message": ctx.message_text, "instruction": ctx.message_text},
            ),
            action,
        )

    return None


def _is_query_surface_frame(frame: ContextFrame) -> bool:
    source = str(frame.metadata.get("source") or "").strip()
    return source == "query" or frame.frame_id.startswith("query_surface_")


def _latest_completed_read_only_account_task(ctx: GateContext) -> TaskSpec | None:
    for task in reversed(list(ctx.state_view.tasks.values())):
        if task.type != "account" or task.stage != TaskStage.COMPLETED:
            continue
        action = str(task.payload.get("action") or "").strip()
        if action in {"check_balance", "balance", "show_balance", "overall_balance"}:
            return task
    return None


def _read_only_refresh_updates(ctx: GateContext, frame: ContextFrame | None) -> RouteResolution | None:
    refresh_spec = _build_read_only_refresh_spec(ctx, frame)
    if refresh_spec is None:
        return None
    task_id, spec, refresh_action = refresh_spec
    logger.info(
        "gate_read_only_refresh_followup_hit",
        frame_type=frame.frame_type.value if frame is not None else None,
        refresh_action=refresh_action,
        task_id=task_id,
    )
    return task_dispatch(
        ctx,
        tasks={task_id: spec},
        waves=[[task_id]],
        owner="guardrail",
        decision="read_only_refresh_followup",
        path_shape="read_only_refresh_followup",
        extra_updates={"pending_interrupt": None},
        target_domain=spec.type,
        mode="continuation",
        source="context_frame_followup",
        heuristic_type="context_frame_shortcut",
        heuristic_name="read_only_refresh",
    )


def _data_plan_redisplay_updates(ctx: GateContext) -> RouteResolution | None:
    frame_followup = build_context_frame_followup_response(
        ctx.state,
        ctx.message_text,
        decision=ContextFrameFollowupDecision(
            decision="show_details",
            confidence=0.94,
            detected_language=ctx.current_locale,
            reason="short visible-context refresh request",
        ),
        locale=ctx.current_locale,
    )
    if frame_followup is None:
        return None
    return _context_frame_followup_updates(ctx, frame_followup)


def _context_frame_followup_eligible(ctx: GateContext) -> bool:
    return (
        not ctx.live_pending_interrupt and not ctx.state_view.has_gate_blocking_state and ctx.task_planner is not None
    )


def _is_contextual_casual_continuation(ctx: GateContext) -> bool:
    return is_contextual_casual_followup_turn(
        ctx.message_text,
        ctx.state_view.loaded_context_or_empty.get("history"),
    )


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
        locale=ctx.current_locale,
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
        locale=ctx.current_locale,
    )
    return decision, frame_followup


async def _stage_context_frame_followup(ctx: GateContext) -> RouteResolution | None:
    """Resolve semantic follow-ups against the latest displayed response frame before domain routing."""
    if not _context_frame_followup_eligible(ctx):
        return None
    if looks_like_obvious_casual_or_meta_turn(ctx.message_text):
        logger.info("gate_context_frame_followup_skipped_for_casual_turn")
        return None
    if _is_contextual_casual_continuation(ctx):
        logger.info("gate_context_frame_followup_skipped_for_contextual_casual_turn")
        return None
    if detect_unsupported_capability(ctx.message_text) is not None:
        logger.info("gate_context_frame_followup_skipped_for_unsupported_capability")
        return None

    frame = ContextFrameManager().latest_active_frame(ctx.state)
    if _looks_like_read_only_refresh_request(ctx.message_text):
        if frame is not None and frame.frame_type == ContextFrameType.DATA_PLAN_LIST:
            logger.info(
                "gate_data_plan_redisplay_followup_hit",
                frame_type=frame.frame_type.value,
                item_count=len(frame.items),
            )
            data_plan_followup = _data_plan_redisplay_updates(ctx)
            if data_plan_followup is not None:
                return data_plan_followup

        bypass_read_only = False
        if await ctx.has_active_query_session():
            logger.info("gate_read_only_refresh_bypassed_for_active_query")
            bypass_read_only = True

        if not bypass_read_only:
            read_only_refresh = _read_only_refresh_updates(ctx, frame)
            if read_only_refresh is not None:
                return read_only_refresh

    if await ctx.has_active_query_session():
        logger.info("gate_context_frame_followup_skipped_for_active_query_session")
        return None

    if frame is None or not frame.items:
        return None
    if _is_query_domain_request(ctx.message_text):
        logger.info(
            "gate_context_frame_followup_skipped_for_fresh_query",
            frame_type=frame.frame_type.value,
            item_count=len(frame.items),
        )
        return None
    if (
        _is_account_balance_request(ctx.message_text)
        or _is_account_domain_request(ctx.message_text)
        or _is_beneficiary_domain_request(ctx.message_text)
    ):
        logger.info(
            "gate_context_frame_followup_skipped_for_fresh_read_domain",
            frame_type=frame.frame_type.value,
            item_count=len(frame.items),
        )
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
