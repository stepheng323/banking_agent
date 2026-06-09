"""Mixed-transaction source-account repairs."""

from __future__ import annotations

import re
from typing import Any

from apps.chat.src.agent.orchestrator.workflows.planner.context.read.context_read_constants import (
    TRANSACTION_EXECUTORS,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_normalizer_parsing import (
    extract_bank_candidates,
    single_unambiguous,
)
from shared.types.planner import PlannedTask

_TRAILING_SOURCE_RE = re.compile(
    r"\b(?:from|using|use|with)\s+(?:my\s+)?(?P<source>[a-z0-9 .&'_-]+?)(?:\s+account)?\s*$",
    re.IGNORECASE,
)
_LEADING_SOURCE_RE = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|please|pls|abeg|oya|jowo|biko|kindly)\s+)*"
    r"(?:use|using|from|with)\s+(?:my\s+)?(?P<source>.+?)\s+"
    r"(?:to\s+)?(?:send|transfer|pay|buy)\b",
    re.IGNORECASE,
)


def _source_bank_display(value: str) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    bank_name, ambiguous = single_unambiguous(extract_bank_candidates(raw))
    if ambiguous:
        return None
    if isinstance(bank_name, str) and bank_name.strip():
        return bank_name.strip()
    return raw


def _source_bank_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _single_source_from_values(values: list[str]) -> str | None:
    candidates: dict[str, str] = {}
    for value in values:
        display = _source_bank_display(value)
        if not display:
            continue
        candidates.setdefault(_source_bank_key(display), display)
    if len(candidates) != 1:
        return None
    return next(iter(candidates.values()))


def _source_bank_from_text(text: str) -> str | None:
    values: list[str] = []
    for pattern in (_TRAILING_SOURCE_RE, _LEADING_SOURCE_RE):
        match = pattern.search(text or "")
        if match is not None:
            values.append(match.group("source"))
    return _single_source_from_values(values)


def _task_source_bank(task: PlannedTask) -> str | None:
    raw = getattr(task.parameters, "source_bank_name", None)
    if not isinstance(raw, str):
        return None
    return raw.strip() or None


def _transaction_tasks(planned_tasks: list[PlannedTask]) -> list[PlannedTask]:
    return [task for task in planned_tasks if task.executor in TRANSACTION_EXECUTORS]


def propagate_mixed_transaction_source_bank(
    planned_tasks: list[PlannedTask],
    text: str,
) -> tuple[list[PlannedTask], dict[str, Any] | None]:
    """Apply one explicit mixed-transaction source bank to sibling tasks missing it."""
    transaction_tasks = _transaction_tasks(planned_tasks)
    if len(transaction_tasks) < 2:
        return planned_tasks, None
    if len({task.executor for task in transaction_tasks}) < 2:
        return planned_tasks, None

    text_source = _source_bank_from_text(text)
    if not text_source:
        return planned_tasks, None
    existing_source = _single_source_from_values(
        [source for task in transaction_tasks if (source := _task_source_bank(task))]
    )
    if existing_source and _source_bank_key(existing_source) != _source_bank_key(text_source):
        return planned_tasks, None
    source_bank = text_source

    changed_task_ids: list[str] = []
    updated_tasks: list[PlannedTask] = []
    for task in planned_tasks:
        if task.executor not in TRANSACTION_EXECUTORS:
            updated_tasks.append(task)
            continue

        if _task_source_bank(task):
            updated_tasks.append(task)
            continue

        if not hasattr(task.parameters, "source_bank_name"):
            updated_tasks.append(task)
            continue

        params = task.parameters.model_copy(deep=True)
        params.source_bank_name = source_bank
        updated_tasks.append(task.model_copy(update={"parameters": params}))
        changed_task_ids.append(task.task_id)

    if not changed_task_ids:
        return planned_tasks, None

    return (
        updated_tasks,
        {
            "source_bank_name": source_bank,
            "changed_task_ids": changed_task_ids,
        },
    )


__all__ = ["propagate_mixed_transaction_source_bank"]
