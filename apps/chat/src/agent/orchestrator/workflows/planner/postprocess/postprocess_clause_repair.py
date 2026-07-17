"""Clause-based planner repair helpers."""

from typing import Any

from apps.chat.src.agent.orchestrator.workflows.planner.core.domains import TRANSACTION_EXECUTORS
from apps.chat.src.agent.orchestrator.workflows.planner.postprocess.postprocess_clause_balance import (
    _balance_like_clause,
    _build_balance_task_from_clause,
)
from apps.chat.src.agent.orchestrator.workflows.planner.postprocess.postprocess_clause_transfer import (
    _build_transfer_task_from_clause,
    _repair_transfer_task_from_clause,
)
from apps.chat.src.agent.orchestrator.workflows.planner.postprocess.postprocess_clause_utils import (
    _is_actionable_clause,
    _normalize_clause_family,
    _resolve_clause_for_task,
    _task_matches_clause,
)
from shared.types.planner import PlannerOutput


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
    repaired_transfer_clause_indexes: list[int] = []

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
        family = _normalize_clause_family(clause.intent_family)
        if family == "transfer":
            transfer_task = _build_transfer_task_from_clause(clause, existing_ids=existing_ids)
            insert_at = next(
                (
                    index
                    for index, task in enumerate(tasks)
                    if isinstance(task.source_clause_index, int) and task.source_clause_index > clause.clause_index
                ),
                len(tasks),
            )
            tasks.insert(insert_at, transfer_task)
            repaired_transfer_clause_indexes.append(clause.clause_index)
        elif _balance_like_clause(clause):
            transaction_depends_on = [task.task_id for task in tasks if task.executor in TRANSACTION_EXECUTORS]
            tasks.append(
                _build_balance_task_from_clause(
                    clause,
                    existing_ids=existing_ids,
                    depends_on=transaction_depends_on,
                )
            )
            repaired_balance_clause_indexes.append(clause.clause_index)

    if not changed_tasks and not repaired_balance_clause_indexes and not repaired_transfer_clause_indexes:
        return planner_output, None

    updated_output = planner_output.model_copy(update={"tasks": tasks})
    if len(tasks) > 1:
        updated_output = updated_output.model_copy(update={"primary_intent": "mixed", "is_complex": True})

    return (
        updated_output,
        {
            "repaired_balance_clause_indexes": repaired_balance_clause_indexes,
            "repaired_transfer_clause_indexes": repaired_transfer_clause_indexes,
            "changed_transfer_task_ids": changed_tasks,
        },
    )


__all__ = ["_validate_and_repair_planner_clauses"]
