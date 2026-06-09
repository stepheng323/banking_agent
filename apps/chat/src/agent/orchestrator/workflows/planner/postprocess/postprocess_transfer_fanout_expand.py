"""Expansion repair for under-produced multi-recipient transfer tasks."""

from typing import Any

from apps.chat.src.agent.orchestrator.utils.task_payload_recipients import derive_recipients_from_user_text
from apps.chat.src.agent.orchestrator.workflows.planner.postprocess.postprocess_transfer_fanout_common import (
    _apply_transfer_fanout_target,
    _next_transfer_fanout_task_id,
)
from shared.money import MoneyAmount
from shared.types.planner import PlannedTask, TransferTaskParameters


def _planned_recipient_allocations(source_task: PlannedTask) -> list[tuple[str, MoneyAmount]] | None:
    if not isinstance(source_task.parameters, TransferTaskParameters):
        return None
    allocations = source_task.parameters.recipient_allocations
    if not allocations or len(allocations) < 2:
        return None

    normalized: list[tuple[str, MoneyAmount]] = []
    for allocation in allocations:
        recipient_name = str(allocation.recipient_name or "").strip()
        if not recipient_name:
            return None
        normalized.append((recipient_name, allocation.amount))
    return normalized if len(normalized) >= 2 else None


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
    if not isinstance(source_task.parameters, TransferTaskParameters):
        return planned_tasks, None
    source_parameters = source_task.parameters

    recipient_allocations = _planned_recipient_allocations(source_task)
    recipients = derive_recipients_from_user_text(user_text)
    if clause_text_by_index:
        clause_text = clause_text_by_index.get(source_task.source_clause_index or 0, "")
        if clause_text:
            clause_recipients = derive_recipients_from_user_text(clause_text)
            recipients = clause_recipients
            if recipient_allocations is None and len(clause_recipients) < 2:
                for fallback_recipients in (
                    derive_recipients_from_user_text(source_task.instruction or ""),
                    derive_recipients_from_user_text(user_text),
                ):
                    if len(fallback_recipients) >= 2:
                        recipients = fallback_recipients
                        break

    # Keep parser repair narrow: avoid fanout when task is account+bank explicit or purely reference-based.
    if source_parameters.recipient_account:
        return planned_tasks, None
    if source_parameters.bank_name and not (recipient_allocations is None and len(recipients) >= 2):
        return planned_tasks, None
    if source_parameters.reference and not (source_parameters.recipient or source_parameters.recipient_name):
        return planned_tasks, None

    if recipient_allocations is None and len(recipients) < 2:
        return planned_tasks, None

    clear_destination_bank = bool(source_parameters.bank_name and recipient_allocations is None)

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
            clear_destination_bank=clear_destination_bank,
        )
    expanded_source_tasks.append(first_task)

    if recipient_allocations:
        extra_recipients: list[tuple[str, MoneyAmount | None]] = [
            (recipient, amount) for recipient, amount in recipient_allocations[1:]
        ]
    else:
        extra_recipients = [(recipient, None) for recipient in recipients[1:]]
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
            clear_destination_bank=clear_destination_bank,
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


__all__ = [
    "_expand_underproduced_transfer_tasks",
]
