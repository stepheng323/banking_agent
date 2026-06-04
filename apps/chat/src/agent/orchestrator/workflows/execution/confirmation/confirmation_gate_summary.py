"""Confirmation gate summary assembly across one or more tasks."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.confirmation.confirmation_task_summary import (
    _render_task_confirmation_summary,
    _strip_batch_name_mismatch_warning,
)
from apps.chat.src.agent.orchestrator.workflows.execution.task_access import (
    existing_tasks,
    require_task,
    required_tasks,
    task_types_for_ids,
)
from banking.presentation.formatters.batch_transfer_summary import format_batch_transfer_summary
from banking.presentation.formatters.confirmation import (
    append_source_account_info,
    build_source_account_info,
    strip_source_account_info_lines,
)
from banking.presentation.formatters.transaction_confirmation_copy import format_confirmation_section


def _build_confirmation_gate_summary(
    *,
    state: OrchestratorState,
    task_ids: list[str],
    locale: str,
    accounts: list[dict[str, Any]],
) -> str:
    if not task_ids:
        return ""

    if len(task_ids) == 1:
        task = require_task(state, task_ids[0])
        return _render_task_confirmation_summary(task=task, locale=locale, accounts=accounts)

    task_types = task_types_for_ids(state, task_ids)
    if task_types == {"transfer"}:
        return _build_batch_transfer_confirmation_summary(
            state=state,
            task_ids=task_ids,
            locale=locale,
            accounts=accounts,
        )

    return _build_mixed_confirmation_summary(
        state=state,
        task_ids=task_ids,
        locale=locale,
        accounts=accounts,
    )


def _build_batch_transfer_confirmation_summary(
    *,
    state: OrchestratorState,
    task_ids: list[str],
    locale: str,
    accounts: list[dict[str, Any]],
) -> str:
    total_amount = 0.0
    source_account_info: str | None = None
    summaries: list[str] = []
    for _tid, task in required_tasks(state, task_ids):
        confirmation_payload = task.payload.get("confirmation") or {}
        snapshot = confirmation_payload.get("snapshot") or {}
        payload_amount = task.payload.get("amount")
        if isinstance(payload_amount, (int, float)):
            total_amount += float(payload_amount)
        elif isinstance(snapshot, dict):
            amount = snapshot.get("amount", 0)
            if isinstance(amount, (int, float)):
                total_amount += float(amount)
        if source_account_info is None:
            source_account_info = build_source_account_info(
                task_payload=task.payload,
                snapshot=snapshot if isinstance(snapshot, dict) else {},
                accounts=accounts,
                locale=locale,
            )
        raw_summary = confirmation_payload.get("summary")
        task_summary = ""
        if isinstance(raw_summary, str):
            task_summary = _strip_batch_name_mismatch_warning(raw_summary, task.payload)
        if not task_summary:
            task_summary = _render_task_confirmation_summary(task=task, locale=locale, accounts=accounts)
        task_summary = strip_source_account_info_lines(task_summary, locale=locale)
        if task_summary:
            summaries.append(task_summary)
    return format_batch_transfer_summary(
        num_transfers=len(task_ids),
        total_amount=total_amount,
        source_account_info=source_account_info,
        summaries=summaries,
        locale=locale,
    )


def _build_mixed_confirmation_summary(
    *,
    state: OrchestratorState,
    task_ids: list[str],
    locale: str,
    accounts: list[dict[str, Any]],
) -> str:
    task_summaries = [
        (task, _render_task_confirmation_summary(task=task, locale=locale, accounts=accounts))
        for _tid, task in existing_tasks(state, task_ids)
    ]
    non_empty = [(task, summary) for task, summary in task_summaries if summary]
    if not non_empty:
        return ""

    source_infos: list[str] = []
    for _tid, task in existing_tasks(state, task_ids):
        confirmation_payload = task.payload.get("confirmation") or {}
        snapshot = confirmation_payload.get("snapshot")
        source_info = build_source_account_info(
            task_payload=task.payload,
            snapshot=snapshot if isinstance(snapshot, dict) else {},
            accounts=accounts,
            locale=locale,
        )
        if source_info:
            source_infos.append(source_info)

    if source_infos and len(set(source_infos)) == 1 and len(non_empty) > 1:
        stripped = [
            format_confirmation_section(
                task_type=task.type,
                summary=strip_source_account_info_lines(summary, locale=locale),
                locale=locale,
            )
            for task, summary in non_empty
        ]
        stripped_non_empty = [summary for summary in stripped if summary]
        merged = "\n\n".join(stripped_non_empty)
        return append_source_account_info(merged, source_infos[0], locale=locale)

    return "\n\n".join(
        format_confirmation_section(task_type=task.type, summary=summary, locale=locale) for task, summary in non_empty
    )


__all__ = ["_build_confirmation_gate_summary"]
