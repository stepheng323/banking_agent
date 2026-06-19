import re
import time
import uuid
from typing import Any

from apps.chat.src.agent.orchestrator.context.models import ContextFrameType
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.beneficiary_suggestions import (
    _resolve_beneficiary_suggestion_reply,
)
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.deterministic import (
    classify_deterministic_meta_response,
)
from apps.chat.src.agent.orchestrator.workflows.gate.core.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.core.outcomes import task_dispatch
from apps.chat.src.agent.orchestrator.workflows.gate.utils.direct_tasks import _next_direct_beneficiary_task_id
from apps.chat.src.agent.orchestrator.workflows.interrupt.signals import _could_be_schedule_interrupt_read_request
from banking.intent.routing_signals import looks_like_support_problem_statement
from banking.transactions.shared.confirmation.classifier import classify_confirmation_reply_sync
from banking.transactions.shared.confirmation.models import ConfirmationDecision
from banking.transactions.shared.confirmation.phrases import normalize_confirmation_locale
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def _stage_beneficiary_suggestion(ctx: GateContext) -> dict[str, Any] | None:
    """Beneficiary save/dismiss from Redis suggestion."""
    if ctx.live_pending_interrupt or not ctx.redis_client:
        return None

    suggestion_key = f"user:{ctx.state_view.phone_number}:beneficiary_suggestion"
    try:
        suggestion_data = await ctx.redis_client.get(suggestion_key)
    except Exception as exc:
        logger.warning("beneficiary_suggestion_lookup_failed", error=str(exc))
        suggestion_data = None

    if not suggestion_data:
        return None

    decision = _resolve_beneficiary_suggestion_reply(
        ctx.message_text,
        locale=ctx.current_locale,
    )
    logger.info(
        "beneficiary_suggestion_gate_decision",
        decision=decision.action,
        reason=decision.reason,
        locale=ctx.current_locale,
        alias_present=bool(decision.alias),
    )
    if decision.action in {"save_default", "save_alias"}:
        task_id = _next_direct_beneficiary_task_id(ctx.state_view.tasks)
        task_payload: dict[str, Any] = {
            "action": "save_beneficiary",
            "instruction": ctx.state_view.last_message_text,
            "message": ctx.state_view.last_message_text,
        }
        if decision.alias:
            task_payload["alias"] = decision.alias
        spec = TaskSpec(
            id=task_id,
            type="beneficiary",
            stage=TaskStage.DRAFT,
            payload=task_payload,
        )
        return task_dispatch(
            ctx,
            tasks={task_id: spec},
            waves=[[task_id]],
            owner="guardrail",
            decision="beneficiary_save",
            extra_updates={"pending_interrupt": None},
            target_domain="beneficiary",
            mode="new",
            route_source="beneficiary_suggestion",
            heuristic_type="guardrail_shortcut",
            heuristic_name="beneficiary_suggestion_reply",
        )

    try:
        await ctx.redis_client.delete(suggestion_key)
    except Exception as exc:
        logger.warning("beneficiary_suggestion_dismiss_delete_failed", error=str(exc))
    else:
        logger.info("beneficiary_suggestion_dismissed", reason=decision.reason)
    return None



def _normalize_resume_reply(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower()).strip(" .,!?:;")


def _resume_reply_locale(ctx: GateContext) -> str | None:
    locale = normalize_confirmation_locale(ctx.current_locale)
    if locale is not None:
        return locale.value
    loaded_context = ctx.state_view.loaded_context_or_empty
    loaded_locale = normalize_confirmation_locale(str(loaded_context.get("language") or ""))
    return loaded_locale.value if loaded_locale is not None else None


async def _classify_resume_prompt_reply(ctx: GateContext) -> ConfirmationDecision:
    locale = _resume_reply_locale(ctx)
    decision = classify_confirmation_reply_sync(ctx.message_text, prompt_kind="resume_prompt", locale=locale)
    if decision.action != "unclear" or decision.reason != "no_match":
        return decision

    if ctx.task_planner is None:
        return decision

    intent = ctx.state_view.latest_stashed_session_intent
    try:
        return await ctx.task_planner.classify_confirmation_reply(
            ctx.message_text,
            prompt_kind="resume_prompt",
            locale=locale,
            context=f"We asked whether to continue a stashed {intent} session.",
            path_label="direct_path",
        )
    except Exception as exc:
        logger.warning("resume_prompt_confirmation_classifier_failed", error=str(exc))
        return decision


def _has_live_resume_prompt_frame(ctx: GateContext) -> bool:
    now = int(time.time())
    for frame in ctx.state_view.context_frames:
        if frame.frame_type != ContextFrameType.GENERIC:
            continue
        if frame.created_at_ts + frame.ttl_seconds <= now:
            continue
        if any(item.data.get("resume_prompt") is True for item in frame.items):
            return True
    return False


def _build_resume_action_updates(ctx: GateContext, *, action: str, semantic_path_shape: str) -> dict[str, Any]:
    task_id = f"orchestrator_{action}_{uuid.uuid4().hex[:8]}"
    spec = TaskSpec(
        id=task_id,
        type="orchestrator",
        stage=TaskStage.DRAFT,
        payload={"action": action},
    )
    return task_dispatch(
        ctx,
        tasks={task_id: spec},
        waves=[[task_id]],
        owner="guardrail",
        decision=semantic_path_shape,
        semantic_path_shape=semantic_path_shape,
        target_domain="orchestrator",
        mode="continuation",
        route_source="resume_prompt_guard",
    )


async def _stage_resume_prompt_action(ctx: GateContext) -> dict[str, Any] | None:
    """Resolve terse replies to a live stashed-session resume prompt."""
    if (
        ctx.live_pending_interrupt
        or ctx.state_view.has_gate_blocking_state
        or not ctx.state_view.has_stashed_sessions
        or not _has_live_resume_prompt_frame(ctx)
    ):
        return None

    normalized = _normalize_resume_reply(ctx.message_text)
    if (
        _could_be_schedule_interrupt_read_request(ctx.message_text)
        or looks_like_support_problem_statement(ctx.message_text)
        or classify_deterministic_meta_response(ctx.message_text) is not None
    ):
        return None

    decision = await _classify_resume_prompt_reply(ctx)
    if decision.action == "approve":
        logger.info("gate_resume_prompt_accept", phrase=normalized, source=decision.source)
        return _build_resume_action_updates(
            ctx,
            action="resume_session",
            semantic_path_shape="resume_session_direct",
        )
    if decision.action == "reject":
        logger.info("gate_resume_prompt_dismiss", phrase=normalized, source=decision.source)
        return _build_resume_action_updates(
            ctx,
            action="dismiss_resume_session",
            semantic_path_shape="dismiss_resume_session_direct",
        )
    return None


__all__ = ["_stage_resume_prompt_action"]
