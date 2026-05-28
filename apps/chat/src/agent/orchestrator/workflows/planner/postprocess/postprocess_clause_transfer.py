"""Transfer-recipient repairs for clause-based planner postprocessing."""

from apps.chat.src.agent.orchestrator.utils.task_payload_recipients import derive_recipients_from_user_text
from apps.chat.src.agent.orchestrator.workflows.planner.postprocess.postprocess_clause_utils import (
    _coerce_clause_field_text,
)
from apps.chat.src.agent.orchestrator.workflows.planner.postprocess.postprocess_transfer_fanout_common import (
    _normalize_recipient_text,
)
from shared.types.planner import PlannedTask, PlannerClause, TaskParameters


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
        derived = derive_recipients_from_user_text(clause.text)
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


__all__ = ["_repair_transfer_task_from_clause"]
