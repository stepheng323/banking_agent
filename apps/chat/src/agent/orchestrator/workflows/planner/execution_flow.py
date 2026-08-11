"""Planner execution flow helpers."""

import inspect
from dataclasses import dataclass
from typing import Any

import redis.asyncio as redis

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner import TaskPlanner
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_prompt_models import PlannerPromptSignals
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_quality import (
    PlannerPlanResult,
    PlannerQualityReport,
)
from apps.chat.src.agent.orchestrator.workflows.planner.execution_cleanup import (
    _clear_stale_beneficiary_suggestion,
)
from apps.chat.src.agent.orchestrator.workflows.planner.execution_locale import (
    _resolve_planner_detected_locale,
)
from apps.chat.src.agent.orchestrator.workflows.planner.guardrails_affirmation import (
    _filter_spurious_affirmation_tasks,
)
from apps.chat.src.agent.orchestrator.workflows.planner.guardrails_beneficiary import (
    _enforce_beneficiary_routing_contract,
)
from apps.chat.src.agent.orchestrator.workflows.planner.guardrails_mandate import (
    _deescalate_mandate_acknowledgement,
)
from apps.chat.src.agent.orchestrator.workflows.planner.state_view import PlannerStateView
from shared.types.planner import PlannerOutput
from shared.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class PlannerExecutionResult:
    planner_output: PlannerOutput
    planner_quality_report: PlannerQualityReport
    current_locale: str
    context_read_updates: dict[str, Any]


async def _plan_tasks_with_optional_quality(
    task_planner: TaskPlanner,
    phone_number: str,
    text: str,
    *,
    planner_context: str,
    prompt_signals: PlannerPromptSignals,
    progress_tracker: Any | None = None,
    progress_metadata: dict[str, Any] | None = None,
) -> PlannerPlanResult:
    await _mark_planner_progress(progress_tracker, progress_metadata)
    return await task_planner.plan_tasks_with_quality(
        phone_number,
        text,
        context=planner_context,
        prompt_signals=prompt_signals,
        path_label="planner_path",
    )


async def _mark_planner_progress(
    progress_tracker: Any | None,
    progress_metadata: dict[str, Any] | None,
) -> None:
    """Announce only the duration of a real planner call, never a shortcut."""
    set_stage = getattr(progress_tracker, "set_stage", None)
    if not callable(set_stage):
        return

    try:
        result = set_stage("planner.planning", stage_metadata=progress_metadata)
        if inspect.isawaitable(result):
            await result
    except Exception as exc:  # Progress delivery must not affect banking work.
        logger.warning("planner_progress_stage_failed", error=str(exc))


def _summarize_planner_output(planner_output: PlannerOutput) -> dict[str, Any]:
    planner_tasks = getattr(planner_output, "tasks", []) or []
    tasks = [
        {
            "task_id": getattr(task, "task_id", None),
            "executor": getattr(task, "executor", None),
            "action": getattr(task, "action", None),
            "risk": getattr(task, "risk", None),
            "depends_on_count": len(getattr(task, "depends_on", []) or []),
            "source_clause_index": getattr(task, "source_clause_index", None),
        }
        for task in planner_tasks
    ]
    clauses = getattr(planner_output, "clauses", []) or []
    return {
        "primary_intent": getattr(planner_output, "primary_intent", None),
        "confidence": getattr(planner_output, "confidence", None),
        "is_complex": getattr(planner_output, "is_complex", None),
        "is_cancellation": getattr(planner_output, "is_cancellation", None),
        "is_confirmation": getattr(planner_output, "is_confirmation", None),
        "detected_language": getattr(planner_output, "detected_language", None),
        "beneficiary_route": getattr(planner_output, "beneficiary_route", None),
        "account_action_hint": getattr(planner_output, "account_action_hint", None),
        "response_key": getattr(planner_output, "response_key", None),
        "response_present": bool(getattr(planner_output, "response", "")),
        "task_count": len(planner_tasks),
        "clause_count": len(clauses),
        "tasks": tasks,
        "notes_present": bool(getattr(planner_output, "notes", "")),
    }


async def _execute_planner_with_context(
    *,
    state: OrchestratorState,
    task_planner: TaskPlanner,
    text: str,
    planner_context: str,
    prompt_signals: PlannerPromptSignals,
    active_intent: str | None,
    current_locale: str,
    redis_client: redis.Redis | None,
    state_view: PlannerStateView,
    progress_tracker: Any | None = None,
    progress_metadata: dict[str, Any] | None = None,
) -> PlannerExecutionResult:
    plan_result = await _plan_tasks_with_optional_quality(
        task_planner,
        state_view.phone_number,
        text,
        planner_context=planner_context,
        prompt_signals=prompt_signals,
        progress_tracker=progress_tracker,
        progress_metadata=progress_metadata,
    )
    planner_output = plan_result.planner_output
    planner_quality_report = plan_result.quality_report
    planner_output = _filter_spurious_affirmation_tasks(
        planner_output,
        active_intent=active_intent,
        pending_interrupt_kind=state_view.pending_interrupt_kind,
    )
    planner_output = _deescalate_mandate_acknowledgement(
        planner_output,
        loaded_context=state_view.loaded_context,
        locale=current_locale,
    )
    planner_output = _enforce_beneficiary_routing_contract(
        planner_output,
        message_id=state_view.last_message_id,
        user_text=text,
        has_beneficiary_suggestion=prompt_signals.has_beneficiary_suggestion,
    )
    logger.info("planner_tasks_generated", **_summarize_planner_output(planner_output))
    logger.info(
        "planner_quality_evaluated",
        clean=planner_quality_report.clean,
        dirty_reasons=list(planner_quality_report.dirty_reasons),
    )

    current_locale = await _resolve_planner_detected_locale(
        state_view=state_view,
        planner_output=planner_output,
        current_locale=current_locale,
        redis_client=redis_client,
    )
    await _sync_progress_locale(progress_tracker, current_locale)
    await _clear_stale_beneficiary_suggestion(
        state_view=state_view,
        planner_context=planner_context,
        planner_output=planner_output,
        redis_client=redis_client,
    )

    return PlannerExecutionResult(
        planner_output=planner_output,
        planner_quality_report=planner_quality_report,
        current_locale=current_locale,
        context_read_updates={},
    )


async def _sync_progress_locale(progress_tracker: Any | None, locale: str) -> None:
    """Keep delayed visible progress in the locale resolved by the planner."""
    set_locale = getattr(progress_tracker, "set_locale", None)
    if not callable(set_locale):
        return
    try:
        result = set_locale(locale)
        if inspect.isawaitable(result):
            await result
    except Exception as exc:  # Progress delivery must not affect banking work.
        logger.warning("planner_progress_locale_update_failed", error_type=type(exc).__name__)


__all__ = [
    "PlannerExecutionResult",
    "_execute_planner_with_context",
]
