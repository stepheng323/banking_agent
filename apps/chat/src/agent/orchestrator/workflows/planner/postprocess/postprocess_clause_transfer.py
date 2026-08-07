"""Transfer-recipient repairs for clause-based planner postprocessing."""

from decimal import Decimal

from apps.chat.src.agent.orchestrator.utils.task_payload_recipients import (
    derive_recipients_from_user_text,
)
from apps.chat.src.agent.orchestrator.workflows.planner.postprocess.postprocess_clause_utils import (
    _coerce_clause_field_text,
)
from apps.chat.src.agent.orchestrator.workflows.planner.postprocess.postprocess_transfer_fanout_common import (
    _normalize_recipient_text,
)
from banking.transfers.extraction.parsers import parse_amount_input
from shared.money import MoneyAmount, to_naira
from shared.types.planner import PlannedTask, PlannerClause, TransferTaskParameters, make_planned_task


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
    if not isinstance(task.parameters, TransferTaskParameters):
        return task, False
    params = task.parameters.model_copy(deep=True)
    if params.is_self is True:
        # ``is_self`` is an authoritative planner signal.  A clause repair
        # must never reintroduce an external recipient from the surrounding
        # mixed turn (for example, the beneficiary of a sibling transfer).
        changed = bool(params.recipient or params.recipient_name)
        params.recipient = None
        params.recipient_name = None
        if changed or task.source_clause_index != clause.clause_index:
            return (
                task.model_copy(
                    update={
                        "parameters": params,
                        "source_clause_index": clause.clause_index,
                    }
                ),
                True,
            )
        return task, False
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


def _amount_from_clause(clause: PlannerClause) -> MoneyAmount | None:
    for key in ("amount", "transfer_amount"):
        value = clause.extracted_fields.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return to_naira(value)
        if isinstance(value, str) and value.strip():
            parsed = parse_amount_input(value)
            if parsed is not None:
                return to_naira(Decimal(str(parsed)))

    parsed = parse_amount_input(clause.text)
    return to_naira(Decimal(str(parsed))) if parsed is not None else None


def _transfer_params_from_clause(clause: PlannerClause) -> TransferTaskParameters:
    is_self = clause.extracted_fields.get("is_self") is True
    recipient_name = None if is_self else _coerce_clause_field_text(clause, "recipient_name", "recipient")
    if not recipient_name and not is_self:
        derived = derive_recipients_from_user_text(clause.text)
        recipient_name = derived[0] if len(derived) == 1 else None

    narration = _coerce_clause_field_text(clause, "narration", "user_note", "description", "reason")
    source_bank_name = _coerce_clause_field_text(clause, "source_bank_name", "source_bank")
    bank_name = _coerce_clause_field_text(clause, "bank_name", "recipient_bank_name", "recipient_bank")
    recipient_account = _coerce_clause_field_text(clause, "recipient_account", "account_number")
    return TransferTaskParameters(
        amount=_amount_from_clause(clause),
        recipient=recipient_name,
        recipient_name=recipient_name,
        narration=narration,
        source_bank_name=source_bank_name,
        bank_name=bank_name,
        recipient_account=recipient_account,
        is_self=is_self or None,
    )


def _build_transfer_task_from_clause(clause: PlannerClause, *, existing_ids: set[str]) -> PlannedTask:
    task_id = clause.task_ids[0] if clause.task_ids and clause.task_ids[0] not in existing_ids else None
    if task_id is None:
        base = f"transfer_clause_{clause.clause_index}"
        task_id = base
        suffix = 2
        while task_id in existing_ids:
            task_id = f"{base}_{suffix}"
            suffix += 1
    existing_ids.add(task_id)

    return make_planned_task(
        task_id=task_id,
        action="send_money",
        executor="transfer",
        instruction=clause.text,
        parameters=_transfer_params_from_clause(clause),
        risk="MONEY_MOVE",
        source_clause_index=clause.clause_index,
    )


__all__ = ["_build_transfer_task_from_clause", "_repair_transfer_task_from_clause"]
