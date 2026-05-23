import time
import uuid
from typing import Any, cast

from apps.chat.src.agent.orchestrator.context.referent_memory import remember_referents_from_stashed_session
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.nodes.interrupt.context import (
    _clear_current_domain_sessions,
    _is_resumable_interrupt,
    _is_transaction_replacement,
    _should_stash_switch,
    logger,
)
from shared.types.planner import (
    PlannerOutput,
)

TRANSACTION_INTENTS = {"transfer", "airtime", "data"}
NON_TRANSACTION_SWITCH_INTENTS = {"query", "account", "faq", "support", "beneficiary"}
KNOWN_SWITCH_INTENTS = TRANSACTION_INTENTS | NON_TRANSACTION_SWITCH_INTENTS
INTERRUPT_REQUIRED_FIELDS_MAX_CHARS = 700
INTERRUPT_PROMPT_MAX_CHARS = 300
INTERRUPT_ACTIVE_TASK_STATE_MAX_CHARS = 700
INTERRUPT_REQUIRED_FIELDS_COMPACT_MAX_CHARS = 240
INTERRUPT_PROMPT_COMPACT_MAX_CHARS = 160
INTERRUPT_ACTIVE_TASK_STATE_COMPACT_MAX_CHARS = 320

def _stash_current_session(
    state: OrchestratorState,
    *,
    interrupt: Any,
    intent: str,
) -> list[dict[str, Any]]:
    stash_id = f"stash_{uuid.uuid4().hex}"
    current_session = {
        "stash_id": stash_id,
        "tasks": state.tasks,
        "waves": state.waves,
        "current_wave_index": state.current_wave_index,
        "pending_interrupt": interrupt,
        "intent": intent,
        "stashed_at_ts": int(time.time()),
    }
    remember_referents_from_stashed_session(state, current_session)
    return cast(list[dict[str, Any]], state.stashed_sessions + [current_session])

def _build_stash_switch_updates(
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
) -> dict[str, Any]:
    stashed = _stash_current_session(state, interrupt=interrupt, intent=active_type)
    cleaned_stack, active_domain = _clear_current_domain_sessions(state, current_task_types)

    logger.info(
        "interrupt_replan_switched",
        kind=interrupt.kind,
        from_types=sorted(current_task_types),
        to_types=sorted(new_task_types),
        stashed=True,
    )
    updates: dict[str, Any] = {
        "pending_interrupt": None,
        "last_interrupt": None,
        "tasks": new_tasks,
        "waves": waves,
        "current_wave_index": 0,
        "normalized_instruction": text,
        "task_results": {},
        "stashed_sessions": stashed,
        "referent_memory": state.referent_memory,
        "session_stack": cleaned_stack,
        "active_domain": active_domain,
        "pin_verified": False,
        "last_callback": None,
    }
    if planner_output is not None:
        updates["planner_output"] = planner_output
    return updates

def _build_replace_switch_updates(
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
    cleaned_stack, active_domain = _clear_current_domain_sessions(state, current_task_types)
    logger.info(
        "interrupt_replan_replaced",
        kind=interrupt.kind,
        from_types=sorted(current_task_types),
        to_types=sorted(new_task_types),
        remaining_session_domains=[session.domain for session in cleaned_stack],
    )
    updates: dict[str, Any] = {
        "pending_interrupt": None,
        "last_interrupt": None,
        "tasks": new_tasks,
        "waves": waves,
        "current_wave_index": 0,
        "normalized_instruction": text,
        "task_results": {},
        "session_stack": cleaned_stack,
        "active_domain": active_domain,
        "pin_verified": False,
        "last_callback": None,
    }
    if planner_output is not None:
        updates["planner_output"] = planner_output
    return updates

def _build_transaction_replacement_updates(
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
    cleaned_stack = [session for session in state.session_stack if session.domain not in TRANSACTION_INTENTS]
    logger.info(
        "interrupt_transaction_replaced",
        kind=interrupt.kind,
        cancelled_task_ids=interrupt.task_ids,
        from_types=sorted(current_task_types),
        to_types=sorted(new_task_types),
        remaining_session_domains=[session.domain for session in cleaned_stack],
    )
    updates: dict[str, Any] = {
        "pending_interrupt": None,
        "last_interrupt": None,
        "tasks": new_tasks,
        "waves": waves,
        "current_wave_index": 0,
        "normalized_instruction": text,
        "task_results": {},
        "session_stack": cleaned_stack,
        "active_domain": cleaned_stack[-1].domain if cleaned_stack else None,
        "pin_verified": False,
        "last_callback": None,
    }
    if planner_output is not None:
        updates["planner_output"] = planner_output
    return updates

def _shared_confirmation_source_payload(
    *,
    state: OrchestratorState,
    active_task_ids: list[str],
) -> dict[str, Any]:
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
        task = state.tasks.get(task_id)
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
    active_task_ids = [str(task_id) for task_id in getattr(interrupt, "task_ids", []) if str(task_id) in state.tasks]
    shared_source = _shared_confirmation_source_payload(state=state, active_task_ids=active_task_ids)
    new_tasks = _inherit_shared_source_for_new_tasks(new_tasks=new_tasks, shared_source=shared_source)
    new_task_ids = [task_id for wave in waves for task_id in wave if task_id in new_tasks]
    merged_wave = list(dict.fromkeys([*active_task_ids, *new_task_ids]))
    merged_tasks = {**state.tasks, **new_tasks}
    cleaned_stack = [session for session in state.session_stack if session.domain not in TRANSACTION_INTENTS]

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
        "task_results": state.task_results,
        "session_stack": cleaned_stack,
        "active_domain": cleaned_stack[-1].domain if cleaned_stack else None,
        "pin_verified": False,
        "last_callback": None,
    }
    if planner_output is not None:
        updates["planner_output"] = planner_output
    return updates

def _build_planner_switch_updates(
    *,
    state: OrchestratorState,
    interrupt: Any,
    active_type: str,
    current_task_types: set[str],
    text: str,
    expected_executors: list[str],
) -> dict[str, Any]:
    cleaned_stack, active_domain = _clear_current_domain_sessions(state, current_task_types)
    updates: dict[str, Any] = {
        "pending_interrupt": None,
        "last_interrupt": interrupt,
        "tasks": {},
        "waves": [],
        "current_wave_index": 0,
        "normalized_instruction": text,
        "planner_output": None,
        "task_results": {},
        "session_stack": cleaned_stack,
        "active_domain": active_domain,
        "pin_verified": False,
        "last_callback": None,
    }
    if expected_executors:
        updates["preplanner_expected_transaction_executors"] = expected_executors

    if current_task_types.issubset(TRANSACTION_INTENTS) and _is_resumable_interrupt(interrupt):
        stashed = _stash_current_session(state, interrupt=interrupt, intent=active_type)
        updates["stashed_sessions"] = stashed
        updates["referent_memory"] = state.referent_memory
        logger.info(
            "interrupt_switch_to_planner_stashed",
            kind=interrupt.kind,
            from_types=sorted(current_task_types),
            expected_executors=expected_executors,
        )
    else:
        logger.info(
            "interrupt_switch_to_planner_replaced",
            kind=interrupt.kind,
            from_types=sorted(current_task_types),
            expected_executors=expected_executors,
        )
    return updates

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
