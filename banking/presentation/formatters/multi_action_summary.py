"""Completed multi-action transaction summary formatting."""

from __future__ import annotations

from typing import Any

from banking.presentation.formatters.recipient_display import format_summary_recipient_display_label
from banking.presentation.formatters.transaction_confirmation_copy import build_completion_frame
from banking.presentation.formatters.transaction_copy_context import derive_task_mix, format_amount_compact
from banking.presentation.i18n.message_keys import MessageKey
from banking.presentation.i18n.renderer import render_message
from shared.messaging.body_blocks import MessageDocument
from shared.security.redaction import mask_account_number
from shared.utils.network_utils import format_network_display_name
from shared.utils.user_error import safe_user_error_message


def format_multi_action_summary(completed_tasks: list, locale: str = "en") -> str:
    """Format a text summary for batch/multi-action transactions."""
    task_types = [getattr(task, "type", "") for task in completed_tasks]
    header, footer = build_completion_frame(
        task_types=task_types,
        locale=locale,
        task_count=len(completed_tasks),
    )
    lines = [header, ""]
    total_spent = 0.0
    task_statuses = [
        _normalize_final_status(str(getattr(task, "payload", {}).get("final_status") or "success").lower())
        for task in completed_tasks
        if isinstance(getattr(task, "payload", None), dict)
    ]
    any_failed = any(status == "failed" for status in task_statuses)
    any_succeeded = any(status == "success" for status in task_statuses)
    any_processing = any(status == "processing" for status in task_statuses)
    task_mix = derive_task_mix(task_types)

    if any_processing:
        lines = [_processing_update_header(task_mix, len(completed_tasks), locale), ""]

    transfer_tasks = [task for task in completed_tasks if task.type == "transfer"]
    airtime_tasks = [task for task in completed_tasks if task.type == "airtime"]
    data_tasks = [task for task in completed_tasks if task.type == "data"]
    other_tasks = [task for task in completed_tasks if task.type not in ["transfer", "airtime", "data"]]

    if transfer_tasks:
        total_spent = _append_transfer_lines(lines, transfer_tasks, locale=locale, total_spent=total_spent)

    if airtime_tasks:
        total_spent = _append_airtime_lines(lines, airtime_tasks, locale=locale, total_spent=total_spent)

    if data_tasks:
        total_spent = _append_data_lines(lines, data_tasks, locale=locale, total_spent=total_spent)

    if other_tasks:
        _append_other_task_lines(lines, other_tasks, locale=locale)

    if len(completed_tasks) > 1 and total_spent > 0:
        lines.append(
            render_message(
                "transaction_summary.multi.total_spent",
                locale,
                {"amount": format_amount_compact(total_spent)},
            )
        )
        lines.append("")

    if any_processing:
        if any_succeeded and any_failed:
            footer = render_message("transaction_summary.multi.processing_footer.success_failed", locale)
        elif any_succeeded:
            footer = render_message("transaction_summary.multi.processing_footer.success", locale)
        elif any_failed:
            footer = render_message("transaction_summary.multi.processing_footer.failed", locale)
        else:
            footer = render_message(
                "transaction_summary.multi.processing_footer.all_processing",
                locale,
                {"count": str(len(completed_tasks))},
            )
    elif any_failed:
        footer_key: MessageKey = (
            "transaction_summary.multi.failed_footer.partial"
            if any_succeeded
            else "transaction_summary.multi.failed_footer.all_failed"
        )
        footer = render_message(footer_key, locale, {"count": str(len(completed_tasks))})
    else:
        # Default success footer
        footer = render_message(
            "transaction_summary.multi.success_footer", locale, {"count": str(len(completed_tasks))}
        )

    lines.append(footer)

    return "\n".join(lines)


def format_multi_action_summary_blocks(completed_tasks: list, locale: str = "en") -> MessageDocument:
    """Format batch/multi-action transactions as mobile-friendly message blocks."""
    task_types = [getattr(task, "type", "") for task in completed_tasks]
    header, footer = build_completion_frame(
        task_types=task_types,
        locale=locale,
        task_count=len(completed_tasks),
    )
    task_statuses = [
        _normalize_final_status(str(getattr(task, "payload", {}).get("final_status") or "success").lower())
        for task in completed_tasks
        if isinstance(getattr(task, "payload", None), dict)
    ]
    any_failed = any(status == "failed" for status in task_statuses)
    any_succeeded = any(status == "success" for status in task_statuses)
    any_processing = any(status == "processing" for status in task_statuses)
    task_mix = derive_task_mix(task_types)

    if any_failed and not any_succeeded and not any_processing:
        header = _failed_header(task_mix, len(completed_tasks), locale)
    elif any_processing:
        header = _processing_update_header(task_mix, len(completed_tasks), locale)

    blocks: MessageDocument = [{"type": "heading", "text": _strip_markup(header)}]
    total_spent = 0.0
    transfer_payloads: list[dict[str, Any]] = []
    for task in completed_tasks:
        payload = getattr(task, "payload", None)
        if str(getattr(task, "type", "") or "") == "transfer" and isinstance(payload, dict):
            transfer_payloads.append(payload)
    transfer_source_lines = [
        source
        for payload in transfer_payloads
        for source in _source_lines_for_payload(payload, locale)
    ]
    distinct_transfer_sources = {source for source in transfer_source_lines if source}
    shared_transfer_source = (
        next(iter(distinct_transfer_sources))
        if transfer_source_lines and len(distinct_transfer_sources) == 1 and all(transfer_source_lines)
        else None
    )

    for task in completed_tasks:
        task_type = str(getattr(task, "type", "") or "")
        payload = getattr(task, "payload", None)
        if not isinstance(payload, dict):
            continue
        if task_type == "transfer":
            total_spent = _append_transfer_blocks(
                blocks,
                payload,
                locale=locale,
                total_spent=total_spent,
                include_source=shared_transfer_source is None,
            )
        elif task_type == "airtime":
            total_spent = _append_airtime_block(blocks, payload, locale=locale, total_spent=total_spent)
        elif task_type == "data":
            total_spent = _append_data_block(blocks, payload, locale=locale, total_spent=total_spent)
        else:
            blocks.append(
                {
                    "type": "text",
                    "text": render_message(
                        "transaction_summary.multi.other_completed_line",
                        locale,
                        {"task_type": task_type.replace("_", " ").title()},
                    ),
                }
            )

    if shared_transfer_source:
        blocks.append({"type": "text", "text": shared_transfer_source})

    if len(completed_tasks) > 1 and total_spent > 0:
        blocks.append(
            {
                "type": "key_value",
                "label": _strip_markup(render_message("transaction_summary.multi.total_spent", locale, {"amount": ""}))
                .replace(":", "")
                .strip(),
                "value": format_amount_compact(total_spent),
            }
        )

    if any_processing:
        if any_succeeded and any_failed:
            footer = render_message("transaction_summary.multi.processing_footer.success_failed", locale)
        elif any_succeeded:
            footer = render_message("transaction_summary.multi.processing_footer.success", locale)
        elif any_failed:
            footer = render_message("transaction_summary.multi.processing_footer.failed", locale)
        else:
            footer = render_message(
                "transaction_summary.multi.processing_footer.all_processing",
                locale,
                {"count": str(len(completed_tasks))},
            )
    elif any_failed:
        footer_key: MessageKey = (
            "transaction_summary.multi.failed_footer.partial"
            if any_succeeded
            else "transaction_summary.multi.failed_footer.all_failed"
        )
        footer = render_message(footer_key, locale, {"count": str(len(completed_tasks))})
    else:
        # Default success footer
        footer = render_message(
            "transaction_summary.multi.success_footer", locale, {"count": str(len(completed_tasks))}
        )

    if footer:
        blocks.append({"type": "text", "text": _strip_markup(footer)})
    return blocks


def _append_transfer_lines(lines: list[str], transfer_tasks: list[Any], *, locale: str, total_spent: float) -> float:
    rendered: list[tuple[str, str | None]] = []
    for task in transfer_tasks:
        recipients = task.payload.get("recipients", [])
        is_batch = task.payload.get("is_batch", False) or len(recipients) > 1

        if is_batch and recipients:
            for recipient_entry in recipients:
                amount = float(recipient_entry.get("amount", 0) or 0)
                total_spent += amount
                rendered.append(
                    (
                        _format_batch_transfer_line(recipient_entry, amount=amount, locale=locale),
                        _source_line(recipient_entry, locale),
                    )
                )
            continue

        amount = float(task.payload.get("amount", 0) or 0)
        status = _normalize_final_status(str(task.payload.get("final_status") or "success").lower())
        if status == "success":
            total_spent += amount
        line = _format_single_transfer_line(task.payload, amount=amount, status=status, locale=locale)
        if status == "failed" and (reason := _failure_reason(task.payload)):
            line = f"{line}\n{_format_failure_reason(reason, locale, task_type=task.type)}"
        rendered.append((line, _source_line(task.payload, locale)))

    distinct_sources = {source for _line, source in rendered if source}
    same_source_for_all = len(distinct_sources) == 1 and bool(rendered) and all(source for _line, source in rendered)
    for line, source in rendered:
        lines.append(line if same_source_for_all or not source else f"{line}\n{source}")
    if same_source_for_all:
        lines.append(next(iter(distinct_sources)))

    lines.append("")
    return total_spent


def _append_transfer_blocks(
    blocks: MessageDocument,
    payload: dict[str, Any],
    *,
    locale: str,
    total_spent: float,
    include_source: bool = True,
) -> float:
    recipients = payload.get("recipients", [])
    is_batch = payload.get("is_batch", False) or (isinstance(recipients, list) and len(recipients) > 1)
    rendered: list[tuple[dict[str, Any], str | None]] = []
    if is_batch and isinstance(recipients, list):
        for recipient_entry in recipients:
            if not isinstance(recipient_entry, dict):
                continue
            amount = float(recipient_entry.get("amount", 0) or 0)
            status = _normalize_final_status(str(recipient_entry.get("status", "success")).lower())
            if status == "success":
                total_spent += amount
            rendered.append(
                (
                    _batch_transfer_block(recipient_entry, amount=amount, status=status, locale=locale),
                    _source_line(recipient_entry, locale),
                )
            )
    else:
        amount = float(payload.get("amount", 0) or 0)
        status = _normalize_final_status(str(payload.get("final_status") or "success").lower())
        if status == "success":
            total_spent += amount
        rendered.append(
            (
                _single_transfer_block(payload, amount=amount, status=status, locale=locale),
                _source_line(payload, locale),
            )
        )

    distinct_sources = {source for _block, source in rendered if source}
    same_source_for_all = include_source and len(distinct_sources) == 1 and bool(rendered) and all(
        source for _block, source in rendered
    )
    for block, source in rendered:
        if include_source and not same_source_for_all and source:
            block["details"] = [source]
        blocks.append(block)
    if same_source_for_all:
        blocks.append({"type": "text", "text": next(iter(distinct_sources))})
    return total_spent


def _format_batch_transfer_line(recipient_entry: dict[str, Any], *, amount: float, locale: str) -> str:
    recipient = _recipient_display_name(recipient_entry, locale)
    bank = str(
        recipient_entry.get("bank_name") or recipient_entry.get("recipient_bank_name") or ""
    ).strip() or render_message("transaction_summary.multi.bank_fallback", locale)
    account = str(
        recipient_entry.get("account") or recipient_entry.get("recipient_account") or ""
    ).strip() or render_message("transaction_summary.multi.account_fallback", locale)
    status = _normalize_final_status(str(recipient_entry.get("status", "success")).lower())
    line = _format_transfer_line(
        amount=amount,
        recipient=recipient,
        bank=bank,
        account=account,
        status=status,
        locale=locale,
    )
    return line


def _batch_transfer_block(
    recipient_entry: dict[str, Any],
    *,
    amount: float,
    status: str,
    locale: str,
) -> dict[str, Any]:
    recipient = _recipient_display_name(recipient_entry, locale)
    bank = str(
        recipient_entry.get("bank_name") or recipient_entry.get("recipient_bank_name") or ""
    ).strip() or render_message("transaction_summary.multi.bank_fallback", locale)
    account = str(
        recipient_entry.get("account") or recipient_entry.get("recipient_account") or ""
    ).strip() or render_message("transaction_summary.multi.account_fallback", locale)
    block = _transfer_block(
        amount=amount,
        recipient=recipient,
        bank=bank,
        account=account,
        status=status,
        reason=_failure_reason(recipient_entry) if status == "failed" else None,
        locale=locale,
    )
    return block


def _format_single_transfer_line(payload: dict[str, Any], *, amount: float, status: str, locale: str) -> str:
    recipient = _recipient_display_name(payload, locale, fallback_key="transaction_summary.multi.recipient_fallback")
    bank = str(payload.get("recipient_bank_name") or "").strip() or render_message(
        "transaction_summary.multi.bank_fallback",
        locale,
    )
    account = str(payload.get("recipient_account") or "").strip() or render_message(
        "transaction_summary.multi.account_fallback",
        locale,
    )
    line = _format_transfer_line(
        amount=amount,
        recipient=recipient,
        bank=bank,
        account=account,
        status=status,
        locale=locale,
    )
    return line


def _single_transfer_block(
    payload: dict[str, Any],
    *,
    amount: float,
    status: str,
    locale: str,
) -> dict[str, Any]:
    recipient = _recipient_display_name(payload, locale, fallback_key="transaction_summary.multi.recipient_fallback")
    bank = str(payload.get("recipient_bank_name") or "").strip() or render_message(
        "transaction_summary.multi.bank_fallback",
        locale,
    )
    account = str(payload.get("recipient_account") or "").strip() or render_message(
        "transaction_summary.multi.account_fallback",
        locale,
    )
    block = _transfer_block(
        amount=amount,
        recipient=recipient,
        bank=bank,
        account=account,
        status=status,
        reason=_failure_reason(payload) if status == "failed" else None,
        locale=locale,
    )
    return block


def _recipient_display_name(
    payload: dict[str, Any],
    locale: str,
    *,
    fallback_key: MessageKey = "transaction_summary.multi.recipient_unknown",
) -> str:
    if payload.get("is_self"):
        bank_name = str(payload.get("recipient_bank_name") or payload.get("bank_name") or "").strip()
        if bank_name:
            return render_message("transfer.resolve.my_bank_name", locale, {"bank_name": bank_name})
    return format_summary_recipient_display_label(
        payload.get("recipient_name") or payload.get("alias"),
        payload.get("recipient_resolved_name") or payload.get("name"),
    ) or render_message(fallback_key, locale)


def _source_line(payload: dict[str, Any], locale: str) -> str:
    bank = str(payload.get("source_bank_name") or "").strip()
    account = mask_account_number(payload.get("source_account_number"))
    if not bank or not account:
        return ""
    return render_message(
        "transfer.format.summary.source_line",
        locale,
        {"source_bank": bank, "last4": account[-4:]},
    )


def _source_lines_for_payload(payload: dict[str, Any], locale: str) -> list[str]:
    recipients = payload.get("recipients")
    is_batch = payload.get("is_batch", False) or (isinstance(recipients, list) and len(recipients) > 1)
    if is_batch and isinstance(recipients, list):
        return [_source_line(item, locale) for item in recipients if isinstance(item, dict)]
    return [_source_line(payload, locale)]


def _transfer_block(
    *,
    amount: float,
    recipient: str,
    bank: str,
    account: str,
    status: str,
    reason: str | None,
    locale: str,
) -> dict[str, Any]:
    block: dict[str, Any] = {
        "type": "transaction_item",
        "status": status,
        "title": f"{format_amount_compact(amount)} → {recipient}",
        "subtitle": f"{bank} • {account}",
    }
    if reason:
        block["reason"] = safe_user_error_message(reason, task_type="transfer", locale=locale)
    return block


def _format_transfer_line(
    *,
    amount: float,
    recipient: str,
    bank: str,
    account: str,
    status: str,
    locale: str,
) -> str:
    return render_message(
        "transaction_summary.multi.transfer_compact_line",
        locale,
        {
            "icon": _status_icon(status),
            "amount": format_amount_compact(amount),
            "recipient": recipient,
            "bank": bank,
            "account": account,
        },
    )


def _append_airtime_lines(lines: list[str], airtime_tasks: list[Any], *, locale: str, total_spent: float) -> float:
    for task in airtime_tasks:
        amount = float(task.payload.get("amount", 0) or 0)
        status = _normalize_final_status(str(task.payload.get("final_status") or "success").lower())
        raw_phone = (
            task.payload.get("phone_number")
            or task.payload.get("recipient_phone")
            or task.payload.get("recipientPhone")
            or task.payload.get("phone")
        )
        phone = str(raw_phone).strip() if raw_phone else ""
        if not phone:
            phone = render_message("transaction_summary.multi.phone_fallback", locale)
        network = format_network_display_name(task.payload.get("network"))
        if status == "success":
            total_spent += amount
        airtime_line = render_message(
            "transaction_summary.multi.airtime_line",
            locale,
            {"amount": format_amount_compact(amount), "phone": phone, "network": network},
        )
        lines.append(f"{_status_icon(status)} {airtime_line}")
        if status == "failed" and (reason := _failure_reason(task.payload)):
            lines.append(_format_failure_reason(reason, locale, task_type=task.type))
    lines.append("")
    return total_spent


def _append_airtime_block(
    blocks: MessageDocument,
    payload: dict[str, Any],
    *,
    locale: str,
    total_spent: float,
) -> float:
    amount = float(payload.get("amount", 0) or 0)
    status = _normalize_final_status(str(payload.get("final_status") or "success").lower())
    raw_phone = (
        payload.get("phone_number")
        or payload.get("recipient_phone")
        or payload.get("recipientPhone")
        or payload.get("phone")
    )
    phone = str(raw_phone).strip() if raw_phone else render_message("transaction_summary.multi.phone_fallback", locale)
    network = format_network_display_name(payload.get("network"))
    if status == "success":
        total_spent += amount
    block: dict[str, Any] = {
        "type": "transaction_item",
        "status": status,
        "title": f"Airtime: {format_amount_compact(amount)}",
        "subtitle": f"{phone} ({network})",
    }
    if status == "failed" and (reason := _failure_reason(payload)):
        block["reason"] = safe_user_error_message(reason, task_type="airtime", locale=locale)
    blocks.append(block)
    return total_spent


def _append_data_lines(lines: list[str], data_tasks: list[Any], *, locale: str, total_spent: float) -> float:
    for task in data_tasks:
        amount = float(task.payload.get("amount", 0) or 0)
        status = _normalize_final_status(str(task.payload.get("final_status") or "success").lower())
        phone = (
            task.payload.get("phone_number")
            or task.payload.get("target_phone")
            or render_message(
                "transaction_summary.multi.phone_fallback",
                locale,
            )
        )
        plan = task.payload.get("plan_name") or render_message(
            "transaction_summary.multi.data_plan_fallback",
            locale,
        )
        if status == "success":
            total_spent += amount
        data_line = render_message(
            "transaction_summary.multi.data_line",
            locale,
            {"plan": plan, "amount": format_amount_compact(amount), "phone": phone},
        )
        lines.append(f"{_status_icon(status)} {data_line}")
        if status == "failed" and (reason := _failure_reason(task.payload)):
            lines.append(_format_failure_reason(reason, locale, task_type=task.type))
    lines.append("")
    return total_spent


def _append_data_block(
    blocks: MessageDocument,
    payload: dict[str, Any],
    *,
    locale: str,
    total_spent: float,
) -> float:
    amount = float(payload.get("amount", 0) or 0)
    status = _normalize_final_status(str(payload.get("final_status") or "success").lower())
    phone = (
        payload.get("phone_number")
        or payload.get("target_phone")
        or render_message("transaction_summary.multi.phone_fallback", locale)
    )
    plan = payload.get("plan_name") or render_message("transaction_summary.multi.data_plan_fallback", locale)
    if status == "success":
        total_spent += amount
    block: dict[str, Any] = {
        "type": "transaction_item",
        "status": status,
        "title": f"Data: {plan}",
        "subtitle": f"{format_amount_compact(amount)} for {phone}",
    }
    if status == "failed" and (reason := _failure_reason(payload)):
        block["reason"] = safe_user_error_message(reason, task_type="data", locale=locale)
    blocks.append(block)
    return total_spent


def _append_other_task_lines(lines: list[str], other_tasks: list[Any], *, locale: str) -> None:
    for task in other_tasks:
        lines.append(
            render_message(
                "transaction_summary.multi.other_completed_line",
                locale,
                {"task_type": task.type.replace("_", " ").title()},
            )
        )
    lines.append("")


def _normalize_final_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized in {"success", "successful", "confirmed", "completed"}:
        return "success"
    if normalized in {"pending", "processing", "queued"}:
        return "processing"
    if normalized in {"failed", "error"}:
        return "failed"
    return normalized or "success"


def _failure_reason(payload: dict[str, Any]) -> str | None:
    for key in ("error_message", "reason", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _processing_update_header(task_mix: str, task_count: int, locale: str) -> str:
    if task_mix == "transfer":
        key: MessageKey = (
            "transaction_summary.multi.update_header.transfer_plural"
            if task_count > 1
            else "transaction_summary.multi.update_header.transfer"
        )
    elif task_mix == "airtime":
        key = "transaction_summary.multi.update_header.airtime"
    elif task_mix == "data":
        key = "transaction_summary.multi.update_header.data"
    else:
        key = "transaction_summary.multi.update_header.generic"
    return render_message(key, locale)


def _failed_header(task_mix: str, task_count: int, locale: str) -> str:
    if locale == "en":
        if task_mix == "transfer":
            return "Transfers failed" if task_count > 1 else "Transfer failed"
        if task_mix == "airtime":
            return "Airtime purchases failed" if task_count > 1 else "Airtime purchase failed"
        if task_mix == "data":
            return "Data purchases failed" if task_count > 1 else "Data purchase failed"
        return "Transactions failed"
    return _processing_update_header(task_mix, task_count, locale)


def _format_failure_reason(reason: str, locale: str, *, task_type: str | None = None) -> str:
    safe_reason = safe_user_error_message(reason, task_type=task_type, locale=locale)
    return render_message("transaction_summary.multi.failure_reason", locale, {"reason": safe_reason})


def _status_icon(status: str) -> str:
    if status == "success":
        return "✓"
    if status == "processing":
        return "…"
    return "✗"


def _strip_markup(text: str) -> str:
    return str(text or "").strip().strip("*_").strip()
