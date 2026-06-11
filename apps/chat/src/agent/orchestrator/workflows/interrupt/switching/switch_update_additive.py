from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.signals import TRANSACTION_INTENTS
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view
from shared.types.planner import PlannerOutput


def _shared_confirmation_source_payload(
    *,
    state: OrchestratorState,
    active_task_ids: list[str],
) -> dict[str, Any]:
    state_view = interrupt_state_view(state)
    source_fields = (
        "source_account_id",
        "source_bank_name",
        "source_account_name",
        "source_account_number",
        "source_affinity_mode",
    )
    shared: dict[str, Any] = {}
    saw_source = False
    for task_id in active_task_ids:
        task = state_view.task(task_id)
        payload = task.payload if task and isinstance(task.payload, dict) else {}
        source_account_id = payload.get("source_account_id")
        if not source_account_id:
            continue
        current = {field: payload.get(field) for field in source_fields if payload.get(field) not in (None, "")}
        if not saw_source:
            shared = current
            saw_source = True
            continue
        if shared.get("source_account_id") != source_account_id:
            return {}
        for field in list(shared):
            if current.get(field) != shared[field]:
                shared.pop(field, None)
    return shared if saw_source else {}


def _inherit_shared_source_for_new_tasks(
    *,
    new_tasks: dict[str, TaskSpec],
    shared_source: dict[str, Any],
) -> dict[str, TaskSpec]:
    if not shared_source:
        return new_tasks

    updated: dict[str, TaskSpec] = {}
    for task_id, task in new_tasks.items():
        if task.type not in TRANSACTION_INTENTS or not isinstance(task.payload, dict):
            updated[task_id] = task
            continue
        payload = dict(task.payload)
        for field, value in shared_source.items():
            payload.setdefault(field, value)
        updated[task_id] = task.model_copy(update={"payload": payload})
    return updated


def _build_confirmation_additive_transaction_updates(
    *,
    state: OrchestratorState,
    interrupt: Any,
    current_task_types: set[str],
    new_tasks: dict[str, TaskSpec],
    waves: list[list[str]],
    new_task_types: set[str],
    text: str,
    planner_output: PlannerOutput | None,
) -> dict[str, Any]:
    state_view = interrupt_state_view(state)
    active_task_ids = state_view.active_task_ids_for_interrupt(interrupt)
    shared_source = _shared_confirmation_source_payload(state=state, active_task_ids=active_task_ids)
    new_tasks = _inherit_shared_source_for_new_tasks(new_tasks=new_tasks, shared_source=shared_source)
    new_task_ids = [task_id for wave in waves for task_id in wave if task_id in new_tasks]
    merged_wave = list(dict.fromkeys([*active_task_ids, *new_task_ids]))
    merged_tasks = {**state_view.tasks, **new_tasks}
    cleaned_stack = state_view.session_stack_without_domains(TRANSACTION_INTENTS)

    logger.info(
        "interrupt_confirmation_additive_transaction_merge",
        kind=getattr(interrupt, "kind", None),
        active_task_ids=active_task_ids,
        added_task_ids=new_task_ids,
        from_types=sorted(current_task_types),
        added_types=sorted(new_task_types),
        inherited_source=bool(shared_source),
    )

    updates: dict[str, Any] = {
        "pending_interrupt": None,
        "last_interrupt": interrupt,
        "tasks": merged_tasks,
        "waves": [merged_wave] if merged_wave else waves,
        "current_wave_index": 0,
        "normalized_instruction": text,
        "task_results": state_view.task_results,
        "session_stack": cleaned_stack,
        "active_domain": cleaned_stack[-1].domain if cleaned_stack else None,
        "pin_verified": False,
        "authorization_context": None,
        "last_callback": None,
    }
    if planner_output is not None:
        updates["planner_output"] = planner_output
    return updates


__all__ = [
    "_build_confirmation_additive_transaction_updates",
    "_inherit_shared_source_for_new_tasks",
    "_shared_confirmation_source_payload",
]
