from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import (
    _is_transaction_replacement,
    _should_stash_switch,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.signals import TRANSACTION_INTENTS
from apps.chat.src.agent.orchestrator.workflows.interrupt.switching.switch_update_additive import (
    _build_confirmation_additive_transaction_updates,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.switching.switch_update_planner import (
    _build_planner_switch_updates,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.switching.switch_update_replacement import (
    _build_replace_switch_updates,
    _build_stash_switch_updates,
    _build_transaction_replacement_updates,
)
from shared.types.planner import PlannerOutput


def _switch_updates(
    *,
    state: OrchestratorState,
    interrupt: Any,
    active_type: str,
    current_task_types: set[str],
    new_tasks: dict[str, TaskSpec],
    waves: list[list[str]],
    new_task_types: set[str],
    text: str,
    planner_output: PlannerOutput | None,
    primary_intent: str | None,
    merge_with_pending_confirmation: bool = False,
) -> dict[str, Any]:
    if (
        merge_with_pending_confirmation
        and getattr(interrupt, "kind", None) == "confirmation"
        and current_task_types.issubset(TRANSACTION_INTENTS)
        and new_task_types.issubset(TRANSACTION_INTENTS)
    ):
        return _build_confirmation_additive_transaction_updates(
            state=state,
            interrupt=interrupt,
            current_task_types=current_task_types,
            new_tasks=new_tasks,
            waves=waves,
            new_task_types=new_task_types,
            text=text,
            planner_output=planner_output,
        )

    if _is_transaction_replacement(
        current_task_types=current_task_types,
        new_task_types=new_task_types,
        primary_intent=primary_intent,
    ):
        return _build_stash_switch_updates(
            state=state,
            interrupt=interrupt,
            active_type=active_type,
            current_task_types=current_task_types,
            new_tasks=new_tasks,
            waves=waves,
            new_task_types=new_task_types,
            text=text,
            planner_output=planner_output,
        )

    if _should_stash_switch(current_task_types, new_task_types):
        return _build_stash_switch_updates(
            state=state,
            interrupt=interrupt,
            active_type=active_type,
            current_task_types=current_task_types,
            new_tasks=new_tasks,
            waves=waves,
            new_task_types=new_task_types,
            text=text,
            planner_output=planner_output,
        )

    return _build_replace_switch_updates(
        state=state,
        interrupt=interrupt,
        current_task_types=current_task_types,
        new_tasks=new_tasks,
        waves=waves,
        new_task_types=new_task_types,
        text=text,
        planner_output=planner_output,
    )


__all__ = [
    "_build_planner_switch_updates",
    "_build_transaction_replacement_updates",
    "_switch_updates",
]
