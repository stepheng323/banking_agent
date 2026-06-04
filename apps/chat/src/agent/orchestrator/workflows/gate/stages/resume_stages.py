import re
import time
import uuid
from typing import Any

from apps.chat.src.agent.orchestrator.context.models import ContextFrameType
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.workflows.gate.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.routing import _route_observability_updates
from banking.transactions.shared.confirmation.classifier import classify_confirmation_reply_sync
from banking.transactions.shared.confirmation.models import ConfirmationDecision
from banking.transactions.shared.confirmation.phrases import normalize_confirmation_locale
from shared.utils.logging import get_logger

logger = get_logger(__name__)


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

    intent = "transaction"
    if ctx.state.stashed_sessions:
        intent = str(ctx.state.stashed_sessions[-1].get("intent") or "transaction")
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
    for frame in ctx.state.context_frames:
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
    return {
        **ctx.gate_updates,
        "tasks": {task_id: spec},
        "waves": [[task_id]],
        "current_wave_index": 0,
        "planner_output": None,
        "direct_path_triggered": True,
        "semantic_path_shape": semantic_path_shape,
        **_route_observability_updates(
            owner="guardrail",
            decision=semantic_path_shape,
            target_domain="orchestrator",
            mode="continuation",
            route_source="resume_prompt_guard",
        ),
    }


async def _stage_resume_prompt_action(ctx: GateContext) -> dict[str, Any] | None:
    """Resolve terse replies to a live stashed-session resume prompt."""
    if (
        ctx.live_pending_interrupt
        or ctx.state_view.has_gate_blocking_state
        or not ctx.state.stashed_sessions
        or not _has_live_resume_prompt_frame(ctx)
    ):
        return None

    normalized = _normalize_resume_reply(ctx.message_text)
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
