"""Confirmation gate summary assembly across one or more tasks."""

from decimal import Decimal
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
from banking.presentation.formatters.recipient_display import format_recipient_display_label
from banking.presentation.formatters.transaction_confirmation_copy import format_confirmation_section
from banking.presentation.formatters.transaction_copy_context import format_amount_compact
from banking.presentation.formatters.transfer_summary import format_transfer_summary
from banking.presentation.i18n.renderer import render_message
from shared.money import MoneyAmount, to_naira


def _transfer_amount(payload: dict[str, Any], snapshot: dict[str, Any]) -> MoneyAmount | None:
    amount = to_naira(payload.get("amount"))
    if amount is not None:
        return amount
    return to_naira(snapshot.get("amount"))


def _batch_transfer_payload_summary(
    *,
    task_payload: dict[str, Any],
    snapshot: dict[str, Any],
    locale: str,
) -> str | None:
    amount = _transfer_amount(task_payload, snapshot)
    recipient_name = str(task_payload.get("recipient_name") or snapshot.get("recipient_name") or "").strip()
    resolved_name = str(
        task_payload.get("recipient_resolved_name") or snapshot.get("recipient_resolved_name") or ""
    ).strip()
    recipient_display = format_recipient_display_label(recipient_name, resolved_name)
    recipient_bank = str(task_payload.get("recipient_bank_name") or snapshot.get("recipient_bank") or "").strip()
    recipient_account = str(task_payload.get("recipient_account") or snapshot.get("recipient_account") or "").strip()
    if amount is None or amount <= 0 or not recipient_display or not recipient_bank or not recipient_account:
        return None

    return format_transfer_summary(
        {
            "amount": amount,
            "recipientName": recipient_display,
            "recipientBank": recipient_bank,
            "recipientAccount": recipient_account,
            "authored_narration": task_payload.get("authored_narration") or snapshot.get("authored_narration"),
            "narration": task_payload.get("narration") or snapshot.get("narration"),
            "user_note": task_payload.get("user_note") or snapshot.get("user_note"),
        },
        include_source=False,
        locale=locale,
        personality_context=None,
    )


def _account_number_for_step(step: dict[str, Any], accounts: list[dict[str, Any]]) -> str:
    account_number = str(
        step.get("account_number")
        or step.get("source_account_number")
        or step.get("account_number_last4")
        or step.get("source_account_number_last4")
        or step.get("last4")
        or ""
    ).strip()
    if account_number:
        return account_number

    account_id = str(step.get("account_id") or "").strip()
    if not account_id:
        return ""
    for account in accounts:
        candidate_ids = {
            str(account.get("id") or "").strip(),
            str(account.get("account_id") or "").strip(),
            str(account.get("mono_account_id") or "").strip(),
        }
        if account_id in candidate_ids:
            return str(
                account.get("account_number")
                or account.get("number")
                or account.get("source_account_number")
                or account.get("account_number_last4")
                or account.get("source_account_number_last4")
                or account.get("last4")
                or ""
            ).strip()
    return ""


def _account_last4(account_number: str) -> str:
    digits = "".join(ch for ch in account_number if ch.isdigit())
    return digits[-4:] if len(digits) >= 4 else "????"


def _batch_funding_summary(
    *,
    tasks: list[tuple[str, Any]],
    accounts: list[dict[str, Any]],
    locale: str,
) -> str | None:
    source_totals: dict[tuple[str, str, str], MoneyAmount] = {}
    has_multi_source_plan = False

    for _task_id, task in tasks:
        funding_plan = task.payload.get("funding_plan")
        if not isinstance(funding_plan, dict):
            continue
        if not funding_plan.get("is_single_source", True):
            has_multi_source_plan = True
        steps = funding_plan.get("steps")
        if not isinstance(steps, list):
            continue
        for raw_step in steps:
            if not isinstance(raw_step, dict):
                continue
            amount = to_naira(raw_step.get("amount"))
            if amount is None or amount <= 0:
                continue
            bank = str(
                raw_step.get("bank_name")
                or render_message("transfer.format.multi_source_summary.bank_fallback", locale)
            ).strip()
            account_id = str(raw_step.get("account_id") or "").strip()
            account_number = _account_number_for_step(raw_step, accounts)
            key = (account_id, bank, account_number)
            source_totals[key] = source_totals.get(key, Decimal("0.00")) + amount

    if not source_totals:
        return None
    if len(source_totals) == 1 and not has_multi_source_plan:
        return None

    lines = [render_message("transfer.format.multi_source_summary.funding_header", locale)]
    for (_account_id, bank, account_number), amount in source_totals.items():
        lines.append(
            render_message(
                "transfer.format.multi_source_summary.funding_item",
                locale,
                {
                    "bank": bank,
                    "last4": _account_last4(account_number),
                    "amount": format_amount_compact(amount),
                },
            )
        )
    return "\n".join(lines)


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
    total_amount = Decimal("0.00")
    source_account_info: str | None = None
    source_account_infos: set[str] = set()
    has_multi_source_funding = False
    summaries: list[str] = []
    transfer_tasks = required_tasks(state, task_ids)
    for _tid, task in transfer_tasks:
        confirmation_payload = task.payload.get("confirmation") or {}
        snapshot = confirmation_payload.get("snapshot") or {}
        snapshot_mapping = snapshot if isinstance(snapshot, dict) else {}
        amount = _transfer_amount(task.payload, snapshot_mapping)
        if amount is not None:
            total_amount += amount
        funding_plan = task.payload.get("funding_plan")
        if isinstance(funding_plan, dict) and not funding_plan.get("is_single_source", True):
            has_multi_source_funding = True
        if not has_multi_source_funding:
            task_source_account_info = build_source_account_info(
                task_payload=task.payload,
                snapshot=snapshot_mapping,
                accounts=accounts,
                locale=locale,
            )
            if task_source_account_info:
                source_account_infos.add(task_source_account_info)
        task_summary = _batch_transfer_payload_summary(
            task_payload=task.payload,
            snapshot=snapshot_mapping,
            locale=locale,
        )
        if not task_summary:
            raw_summary = confirmation_payload.get("summary")
            if isinstance(raw_summary, str):
                task_summary = _strip_batch_name_mismatch_warning(raw_summary, task.payload)
        if not task_summary:
            task_summary = _render_task_confirmation_summary(task=task, locale=locale, accounts=accounts)
        task_summary = strip_source_account_info_lines(task_summary, locale=locale)
        if task_summary:
            summaries.append(task_summary)
    if not has_multi_source_funding and len(source_account_infos) == 1:
        source_account_info = next(iter(source_account_infos))
    return format_batch_transfer_summary(
        num_transfers=len(task_ids),
        total_amount=total_amount,
        source_account_info=source_account_info,
        summaries=summaries,
        funding_info=_batch_funding_summary(tasks=transfer_tasks, accounts=accounts, locale=locale),
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
