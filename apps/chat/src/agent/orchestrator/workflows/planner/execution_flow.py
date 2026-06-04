"""Planner execution flow helpers."""

from dataclasses import dataclass
from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.planning.task_planner_prompt_models import PlannerPromptSignals
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_read_frames import (
    _build_beneficiary_context_read_updates,
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
from shared.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class PlannerExecutionResult:
    planner_output: Any
    current_locale: str
    context_read_updates: dict[str, Any]


async def _execute_planner_with_context(
    *,
    state: OrchestratorState,
    task_planner: Any,
    text: str,
    planner_context: str,
    prompt_signals: PlannerPromptSignals,
    active_intent: str | None,
    current_locale: str,
    redis_client: Any | None,
    state_view: PlannerStateView,
) -> PlannerExecutionResult:
    planner_output = await task_planner.plan_tasks(
        state_view.phone_number,
        text,
        context=planner_context,
        prompt_signals=prompt_signals,
        path_label="planner_path",
    )
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
    logger.info("planner_tasks_generated", output=planner_output)

    context_read_subtype = _apply_context_read_planner_shape(
        state=state,
        planner_output=planner_output,
        text=text,
        current_locale=current_locale,
    )
    current_locale = await _resolve_planner_detected_locale(
        state_view=state_view,
        planner_output=planner_output,
        current_locale=current_locale,
        redis_client=redis_client,
    )
    await _clear_stale_beneficiary_suggestion(
        state_view=state_view,
        planner_context=planner_context,
        planner_output=planner_output,
        redis_client=redis_client,
    )

    context_read_updates = _build_beneficiary_context_read_updates(
        state,
        planner_output,
        context_read_subtype,
    )
    return PlannerExecutionResult(
        planner_output=planner_output,
        current_locale=current_locale,
        context_read_updates=context_read_updates,
    )


__all__ = [
    "PlannerExecutionResult",
    "_execute_planner_with_context",
]
