"""Planner execution flow helpers."""

from dataclasses import dataclass
from typing import Any

import redis.asyncio as redis

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.context.read.context_read_frames import (
    _build_beneficiary_context_read_updates,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner import TaskPlanner
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_prompt_models import PlannerPromptSignals
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_quality import (
    PlannerPlanResult,
    PlannerQualityReport,
)
from apps.chat.src.agent.orchestrator.workflows.planner.execution_cleanup import (
    _clear_stale_beneficiary_suggestion,
)
from apps.chat.src.agent.orchestrator.workflows.planner.execution_context_read import (
    _apply_context_read_planner_shape,
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
) -> PlannerPlanResult:
    planner_with_quality = getattr(task_planner, "plan_tasks_with_quality", None)
    if callable(planner_with_quality):
        return await planner_with_quality(
            phone_number,
            text,
            context=planner_context,
            prompt_signals=prompt_signals,
            path_label="planner_path",
        )

    legacy_plan_tasks = getattr(task_planner, "plan_tasks", None)
    if not callable(legacy_plan_tasks):
        msg = "Task planner does not expose plan_tasks_with_quality or legacy plan_tasks"
        raise AttributeError(msg)

    planner_output = await legacy_plan_tasks(
        phone_number,
        text,
        context=planner_context,
        prompt_signals=prompt_signals,
        path_label="planner_path",
    )
    return PlannerPlanResult(
        raw_output=planner_output,
        planner_output=planner_output,
        quality_report=PlannerQualityReport(),
    )


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
        "context_read_subtype": getattr(planner_output, "context_read_subtype", None),
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
) -> PlannerExecutionResult:
    plan_result = await _plan_tasks_with_optional_quality(
        task_planner,
        state_view.phone_number,
        text,
        planner_context=planner_context,
        prompt_signals=prompt_signals,
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
    context_read_subtype = _apply_context_read_planner_shape(
        state_view=state_view,
        planner_output=planner_output,
        text=text,
        current_locale=current_locale,
    )
    await _clear_stale_beneficiary_suggestion(
        state_view=state_view,
        planner_context=planner_context,
        planner_output=planner_output,
        redis_client=redis_client,
    )

    context_read_updates = _build_beneficiary_context_read_updates(
        state_view,
        planner_output,
        context_read_subtype,
    )
    return PlannerExecutionResult(
        planner_output=planner_output,
        planner_quality_report=planner_quality_report,
        current_locale=current_locale,
        context_read_updates=context_read_updates,
    )


__all__ = [
    "PlannerExecutionResult",
    "_execute_planner_with_context",
]
