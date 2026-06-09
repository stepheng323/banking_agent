"""Shared helpers for multi-recipient transfer post-processing."""

import re

from shared.money import MoneyAmount
from shared.types.planner import PlannedTask, TransferTaskParameters


def _normalize_recipient_text(value: str | None) -> str:
    if not value:
        return ""
    lowered = re.sub(r"([a-z])['’]s\b", r"\1", value.lower())
    return re.sub(r"[^a-z0-9]+", " ", lowered).strip()


def _recipient_overlap_score(left: str | None, right: str | None) -> int:
    left_tokens = {token for token in _normalize_recipient_text(left).split() if token}
    right_tokens = {token for token in _normalize_recipient_text(right).split() if token}
    return len(left_tokens & right_tokens)


def _apply_transfer_fanout_target(
    task: PlannedTask,
    *,
    recipient_name: str,
    amount: MoneyAmount | None,
    clear_source_recipient_allocations: bool,
    binding_index: int,
    clear_destination_bank: bool = False,
) -> None:
    if not isinstance(task.parameters, TransferTaskParameters):
        return
    task.parameters.recipient = recipient_name
    task.parameters.recipient_name = recipient_name
    task.parameters.recipient_allocations = None
    task.parameters.recipient_binding_source = "fanout"
    task.parameters.recipient_binding_index = binding_index
    if clear_destination_bank:
        task.parameters.bank_name = None
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


__all__ = [
    "_apply_transfer_fanout_target",
    "_next_transfer_fanout_task_id",
    "_normalize_recipient_text",
    "_recipient_overlap_score",
]
