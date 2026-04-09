"""Planner task post-processing helpers.

Postprocessing here is intentionally narrow:
- canonicalize execution structure
- repair obvious underproduction
- preserve authoritative bindings

It must not invent new task semantics from free text.
"""

import re
from typing import Any

from apps.core.src.agent.orchestrator.models.domain import TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.planner_context_read import TRANSACTION_EXECUTORS
from apps.core.src.agent.orchestrator.utils.task_payload import _derive_recipients_from_user_text
from shared.types.planner import PlannedTask, PlannerClause, PlannerOutput, TaskParameters


def _normalize_recipient_text(value: str | None) -> str:
    if not value:
        return ""
    lowered = re.sub(r"([a-z])['’]s\b", r"\1", value.lower())
    return re.sub(r"[^a-z0-9]+", " ", lowered).strip()


def _normalize_clause_family(value: str | None) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"account", "balance", "balance_query", "account_balance"}:
        return "account_query"
    if normalized in {"transaction_query", "transactions"}:
        return "query"
    return normalized or "unknown"


def _clause_to_executor_family(clause: PlannerClause) -> str | None:
    family = _normalize_clause_family(clause.intent_family)
    if family in {"transfer", "airtime", "data", "support", "faq", "beneficiary"}:
        return family
    if family == "account_query":
        return "account_or_query"
    if family == "query":
        return "query"
    return None


def _is_actionable_clause(clause: PlannerClause) -> bool:
    return _clause_to_executor_family(clause) is not None


def _task_matches_clause(task: PlannedTask, clause: PlannerClause) -> bool:
    family = _clause_to_executor_family(clause)
    if family is None:
        return False
    if family == "account_or_query":
        return task.executor in {"account", "query"}
    return task.executor == family


def _next_clause_repair_task_id(prefix: str, existing_ids: set[str], clause_index: int) -> str:
    candidate = f"{prefix}_clause_{clause_index}"
    if candidate not in existing_ids:
        existing_ids.add(candidate)
        return candidate
    suffix = 2
    while True:
        candidate = f"{prefix}_clause_{clause_index}_{suffix}"
        if candidate not in existing_ids:
            existing_ids.add(candidate)
            return candidate
        suffix += 1


def _coerce_clause_field_text(clause: PlannerClause, *keys: str) -> str | None:
    for key in keys:
        value = clause.extracted_fields.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _balance_like_clause(clause: PlannerClause) -> bool:
    family = _normalize_clause_family(clause.intent_family)
    if family != "account_query":
        return False
    action_hint = _coerce_clause_field_text(clause, "account_action_hint", "action", "query_kind")
    if action_hint and action_hint.strip().lower().replace(" ", "_") in {
        "check_balance",
        "balance",
        "show_balance",
        "overall_balance",
    }:
        return True
    normalized_text = _normalize_recipient_text(clause.text)
    return any(token in normalized_text for token in ("balance", "remaining", "remain", "left"))


def _build_balance_task_from_clause(
    clause: PlannerClause,
    *,
    existing_ids: set[str],
    depends_on: list[str],
) -> PlannedTask:
    return PlannedTask(
        task_id=_next_clause_repair_task_id("account", existing_ids, clause.clause_index),
        action="check_balance",
        executor="account",
        instruction=clause.text or "Show my balance",
        parameters=TaskParameters(),
        depends_on=depends_on,
        risk="READ_ONLY",
        source_clause_index=clause.clause_index,
    )


def _resolve_clause_for_task(task: PlannedTask, clauses: list[PlannerClause]) -> PlannerClause | None:
    if isinstance(task.source_clause_index, int) and task.source_clause_index > 0:
        for clause in clauses:
            if clause.clause_index == task.source_clause_index:
                return clause
    compatible = [clause for clause in clauses if _task_matches_clause(task, clause)]
    if len(compatible) == 1:
        return compatible[0]
    return None


def _looks_like_cross_clause_recipient_leak(
    recipient_name: str | None,
    *,
    non_transfer_clauses: list[PlannerClause],
) -> bool:
    normalized = _normalize_recipient_text(recipient_name)
    if not normalized:
        return False
    if any(token in normalized for token in ("balance", "remaining", "left")):
        return True
    for clause in non_transfer_clauses:
        clause_text = _normalize_recipient_text(clause.text)
        if clause_text and (normalized == clause_text or clause_text in normalized or normalized in clause_text):
            return True
    return False


def _repair_transfer_task_from_clause(
    task: PlannedTask,
    *,
    clause: PlannerClause,
    non_transfer_clauses: list[PlannerClause],
) -> tuple[PlannedTask, bool]:
    params = task.parameters.model_copy(deep=True) if task.parameters else TaskParameters()
    current_recipient = str(params.recipient_name or params.recipient or "").strip()
    if not _looks_like_cross_clause_recipient_leak(current_recipient, non_transfer_clauses=non_transfer_clauses):
        if task.source_clause_index == clause.clause_index:
            return task, False
        return task.model_copy(update={"source_clause_index": clause.clause_index}), True

    clause_recipient = _coerce_clause_field_text(clause, "recipient_name", "recipient")
    if not clause_recipient:
        derived = _derive_recipients_from_user_text(clause.text)
        clause_recipient = derived[0] if len(derived) == 1 else None
    if not clause_recipient:
        return task, False

    params.recipient = clause_recipient
    params.recipient_name = clause_recipient
    repaired_instruction = clause.text or task.instruction
    return (
        task.model_copy(
            update={
                "parameters": params,
                "instruction": repaired_instruction,
                "source_clause_index": clause.clause_index,
            }
        ),
        True,
    )


def _recipient_overlap_score(left: str | None, right: str | None) -> int:
    left_tokens = {token for token in _normalize_recipient_text(left).split() if token}
    right_tokens = {token for token in _normalize_recipient_text(right).split() if token}
    return len(left_tokens & right_tokens)


def _planned_recipient_allocations(source_task: PlannedTask) -> list[tuple[str, float]] | None:
    allocations = source_task.parameters.recipient_allocations
    if not allocations or len(allocations) < 2:
        return None

    normalized: list[tuple[str, float]] = []
    for allocation in allocations:
        recipient_name = str(allocation.recipient_name or "").strip()
        if not recipient_name:
            return None
        normalized.append((recipient_name, float(allocation.amount)))
    return normalized if len(normalized) >= 2 else None


def _apply_transfer_fanout_target(
    task: PlannedTask,
    *,
    recipient_name: str,
    amount: float | None,
    clear_source_recipient_allocations: bool,
    binding_index: int,
) -> None:
    task.parameters.recipient = recipient_name
    task.parameters.recipient_name = recipient_name
    task.parameters.recipient_allocations = None
    task.parameters.recipient_binding_source = "fanout"
    task.parameters.recipient_binding_index = binding_index
    if amount is not None:
        task.parameters.amount = amount
    if clear_source_recipient_allocations:
        task.parameters.explicit_split = None


def _next_transfer_fanout_task_id(base_task_id: str, index: int, existing_ids: set[str]) -> str:
    candidate = f"{base_task_id}_r{index}"
    while candidate in existing_ids:
        index += 1
        candidate = f"{base_task_id}_r{index}"
    existing_ids.add(candidate)
    return candidate


def _expand_underproduced_transfer_tasks(
    planned_tasks: list[PlannedTask],
    user_text: str,
    *,
    clause_text_by_index: dict[int, str] | None = None,
) -> tuple[list[PlannedTask], dict[str, Any] | None]:
    """Fan out a single transfer task when user text clearly contains multiple recipients."""
    transfer_indices = [idx for idx, task in enumerate(planned_tasks) if task.executor == "transfer"]
    if len(transfer_indices) != 1:
        return planned_tasks, None

    source_index = transfer_indices[0]
    source_task = planned_tasks[source_index]
    source_parameters = source_task.parameters

    # Keep parser repair narrow: avoid fanout when task is account+bank explicit or purely reference-based.
    if source_parameters.recipient_account or source_parameters.bank_name:
        return planned_tasks, None
    if source_parameters.reference and not (source_parameters.recipient or source_parameters.recipient_name):
        return planned_tasks, None

    recipient_allocations = _planned_recipient_allocations(source_task)
    source_text = (
        clause_text_by_index.get(source_task.source_clause_index or 0, user_text)
        if clause_text_by_index
        else user_text
    )
    recipients = _derive_recipients_from_user_text(source_text)
    if recipient_allocations is None and len(recipients) < 2:
        return planned_tasks, None

    existing_ids = {task.task_id for task in planned_tasks}
    expanded_task_ids: list[str] = [source_task.task_id]
    expanded_source_tasks: list[PlannedTask] = []

    first_task = source_task.model_copy(deep=True)
    if recipient_allocations:
        _apply_transfer_fanout_target(
            first_task,
            recipient_name=recipient_allocations[0][0],
            amount=recipient_allocations[0][1],
            clear_source_recipient_allocations=True,
            binding_index=1,
        )
    else:
        _apply_transfer_fanout_target(
            first_task,
            recipient_name=recipients[0],
            amount=None,
            clear_source_recipient_allocations=False,
            binding_index=1,
        )
    expanded_source_tasks.append(first_task)

    extra_recipients = (
        recipient_allocations[1:]
        if recipient_allocations
        else [(recipient, None) for recipient in recipients[1:]]
    )
    for idx, recipient_info in enumerate(extra_recipients, start=2):
        recipient, allocated_amount = recipient_info
        clone = source_task.model_copy(deep=True)
        clone.task_id = _next_transfer_fanout_task_id(source_task.task_id, idx, existing_ids)
        _apply_transfer_fanout_target(
            clone,
            recipient_name=recipient,
            amount=allocated_amount,
            clear_source_recipient_allocations=allocated_amount is not None,
            binding_index=idx,
        )
        expanded_source_tasks.append(clone)
        expanded_task_ids.append(clone.task_id)

    expanded_tasks: list[PlannedTask] = []
    for idx, task in enumerate(planned_tasks):
        if idx == source_index:
            expanded_tasks.extend(expanded_source_tasks)
            continue

        copy_task = task.model_copy(deep=True)
        if source_task.task_id in copy_task.depends_on:
            rewritten: list[str] = []
            for dep in copy_task.depends_on:
                if dep == source_task.task_id:
                    rewritten.extend(expanded_task_ids)
                else:
                    rewritten.append(dep)
            copy_task.depends_on = list(dict.fromkeys(rewritten))
        expanded_tasks.append(copy_task)

    return (
        expanded_tasks,
        {
            "source_task_id": source_task.task_id,
            "recipient_count": len(expanded_task_ids),
            "recipient_names": [item[0] for item in recipient_allocations] if recipient_allocations else recipients,
            "fanout_mode": "recipient_split" if recipient_allocations else "multi_recipient",
        },
    )


def _reconcile_multi_transfer_recipient_tasks(
    planned_tasks: list[PlannedTask],
    user_text: str,
) -> tuple[list[PlannedTask], dict[str, Any] | None]:
    """Repair obvious recipient drift when planner already emitted multiple transfer tasks.

    This is intentionally conservative. It only applies when:
    - user text clearly contains an ordered multi-recipient list
    - planner already emitted the same number of transfer tasks
    - each task can be matched unambiguously to one recipient
    """

    recipients = _derive_recipients_from_user_text(user_text)
    if len(recipients) < 2:
        return planned_tasks, None

    transfer_indices = [idx for idx, task in enumerate(planned_tasks) if task.executor == "transfer"]
    if len(transfer_indices) < 2 or len(transfer_indices) != len(recipients):
        return planned_tasks, None

    transfer_tasks = [planned_tasks[idx] for idx in transfer_indices]
    if any(task.parameters.recipient_allocations for task in transfer_tasks):
        return planned_tasks, None
    if any(task.parameters.recipient_account or task.parameters.bank_name for task in transfer_tasks):
        return planned_tasks, None

    recipient_lookup = {_normalize_recipient_text(recipient): recipient for recipient in recipients}
    if len(recipient_lookup) != len(recipients):
        return planned_tasks, None

    assignments: dict[int, str] = {}
    used_recipient_keys: set[str] = set()

    for idx in transfer_indices:
        task = planned_tasks[idx]
        current_name = str(task.parameters.recipient_name or task.parameters.recipient or "").strip()
        current_key = _normalize_recipient_text(current_name)
        if current_key and current_key in recipient_lookup and current_key not in used_recipient_keys:
            assignments[idx] = current_name
            used_recipient_keys.add(current_key)

    for idx in transfer_indices:
        if idx in assignments:
            continue

        task = planned_tasks[idx]
        current_name = str(task.parameters.recipient_name or task.parameters.recipient or "").strip()
        available = [
            (recipient_key, recipient_lookup[recipient_key])
            for recipient_key in recipient_lookup
            if recipient_key not in used_recipient_keys
        ]
        if not available:
            return planned_tasks, None

        scored = [
            (recipient_key, recipient_name, _recipient_overlap_score(current_name, recipient_name))
            for recipient_key, recipient_name in available
        ]
        scored.sort(key=lambda item: item[2], reverse=True)
        best_key, best_name, best_score = scored[0]
        if best_score <= 0:
            return planned_tasks, None
        if len(scored) > 1 and scored[1][2] == best_score:
            return planned_tasks, None

        assignments[idx] = best_name
        used_recipient_keys.add(best_key)

    changed_tasks: list[tuple[str, str, str]] = []
    normalized_tasks = [task.model_copy(deep=True) for task in planned_tasks]
    for idx in transfer_indices:
        task = normalized_tasks[idx]
        previous_name = str(task.parameters.recipient_name or task.parameters.recipient or "").strip()
        next_name = assignments.get(idx)
        if not next_name or previous_name == next_name:
            continue
        task.parameters.recipient = next_name
        task.parameters.recipient_name = next_name
        changed_tasks.append((task.task_id, previous_name, next_name))

    if not changed_tasks:
        return planned_tasks, None

    return (
        normalized_tasks,
        {
            "recipient_count": len(recipients),
            "recipient_names": recipients,
            "changed_tasks": changed_tasks,
            "repair_mode": "multi_transfer_recipient_reconcile",
        },
    )


def _validate_and_repair_planner_clauses(
    planner_output: PlannerOutput,
) -> tuple[PlannerOutput, dict[str, Any] | None]:
    clauses = [clause for clause in getattr(planner_output, "clauses", []) if _is_actionable_clause(clause)]
    if not clauses:
        return planner_output, None

    tasks = [task.model_copy(deep=True) for task in planner_output.tasks]
    existing_ids = {task.task_id for task in tasks}
    non_transfer_clauses = [
        clause for clause in clauses if _normalize_clause_family(clause.intent_family) != "transfer"
    ]
    changed_tasks: list[str] = []
    repaired_balance_clause_indexes: list[int] = []

    for index, task in enumerate(tasks):
        if task.executor != "transfer":
            continue
        matched_clause = _resolve_clause_for_task(task, clauses)
        if matched_clause is None or _normalize_clause_family(matched_clause.intent_family) != "transfer":
            continue
        repaired_task, repaired = _repair_transfer_task_from_clause(
            task,
            clause=matched_clause,
            non_transfer_clauses=non_transfer_clauses,
        )
        if repaired:
            tasks[index] = repaired_task
            changed_tasks.append(task.task_id)

    for clause in clauses:
        if any(_task_matches_clause(task, clause) for task in tasks):
            continue
        if _balance_like_clause(clause):
            transaction_depends_on = [task.task_id for task in tasks if task.executor in TRANSACTION_EXECUTORS]
            tasks.append(
                _build_balance_task_from_clause(
                    clause,
                    existing_ids=existing_ids,
                    depends_on=transaction_depends_on,
                )
            )
            repaired_balance_clause_indexes.append(clause.clause_index)

    if not changed_tasks and not repaired_balance_clause_indexes:
        return planner_output, None

    updated_output = planner_output.model_copy(update={"tasks": tasks})
    if len(tasks) > 1:
        updated_output = updated_output.model_copy(update={"primary_intent": "mixed", "is_complex": True})

    return (
        updated_output,
        {
            "repaired_balance_clause_indexes": repaired_balance_clause_indexes,
            "changed_transfer_task_ids": changed_tasks,
        },
    )


def _should_replan_active_wave(state: OrchestratorState) -> bool:
    """Allow replanning active waves only for explicit pre-execution update turns."""
    if not state.waves or state.pending_interrupt is not None:
        return False
    if not (state.last_message_text or "").strip():
        return False
    if state.current_wave_index >= len(state.waves):
        return False

    current_wave = state.waves[state.current_wave_index]
    if not current_wave:
        return False

    replannable_stages = {TaskStage.AWAITING_CONFIRMATION, TaskStage.AWAITING_AUTH}
    active_tasks = [state.tasks.get(task_id) for task_id in current_wave if state.tasks.get(task_id) is not None]
    if not active_tasks:
        return False
    return all(task.stage in replannable_stages for task in active_tasks)


def _strip_transactional_depends_on_edges(
    planned_tasks: list[PlannedTask],
) -> tuple[list[PlannedTask], list[tuple[str, str]]]:
    """Remove depends_on edges between transaction tasks for single-batch auth collection."""
    executor_by_task_id = {task.task_id: task.executor for task in planned_tasks}
    stripped_edges: list[tuple[str, str]] = []
    normalized_tasks: list[PlannedTask] = []

    for task in planned_tasks:
        copy_task = task.model_copy(deep=True)
        if copy_task.executor not in TRANSACTION_EXECUTORS:
            normalized_tasks.append(copy_task)
            continue

        next_depends_on: list[str] = []
        seen: set[str] = set()
        for dep_task_id in copy_task.depends_on:
            dep_executor = executor_by_task_id.get(dep_task_id)
            if dep_executor in TRANSACTION_EXECUTORS:
                stripped_edges.append((dep_task_id, copy_task.task_id))
                continue
            if dep_task_id in seen:
                continue
            seen.add(dep_task_id)
            next_depends_on.append(dep_task_id)
        copy_task.depends_on = next_depends_on
        normalized_tasks.append(copy_task)

    return normalized_tasks, stripped_edges


__all__ = [
    "_expand_underproduced_transfer_tasks",
    "_reconcile_multi_transfer_recipient_tasks",
    "_should_replan_active_wave",
    "_strip_transactional_depends_on_edges",
    "_validate_and_repair_planner_clauses",
]
