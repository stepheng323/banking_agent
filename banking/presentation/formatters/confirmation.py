"""Shared confirmation summary formatting helpers."""

from typing import Any

from banking.presentation.formatters.accounts import format_source_account_info_from_account_number
from banking.presentation.i18n.renderer import render_message


def _coerce_cached_balance(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("₦", "").replace("NGN", "").strip()
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _extract_balance_from_mapping(data: dict[str, Any]) -> float | None:
    for key in ("available_balance", "balance", "ledger_balance", "current_balance"):
        if key in data:
            parsed = _coerce_cached_balance(data.get(key))
            if parsed is not None:
                return parsed
    return None


def _extract_cached_balance(account: dict[str, Any] | None) -> float | None:
    if not account:
        return None
    parsed = _extract_balance_from_mapping(account)
    if parsed is not None:
        return parsed

    extra_data = account.get("extra_data")
    if isinstance(extra_data, dict):
        return _extract_balance_from_mapping(extra_data)
    return None


def _account_number_or_last4(account: dict[str, Any] | None) -> Any:
    if not account:
        return None
    return (
        account.get("account_number")
        or account.get("number")
        or account.get("source_account_number")
        or account.get("account_number_last4")
        or account.get("last4")
    )


def _resolve_source_account(
    accounts: list[dict[str, Any]],
    source_account_id: str | None,
    source_account_number: str | None,
) -> dict[str, Any] | None:
    if source_account_id:
        for account in accounts:
            account_id = account.get("id") or account.get("account_id")
            if account_id is not None and str(account_id) == source_account_id:
                return account

    if source_account_number:
        source_num = str(source_account_number)
        for account in accounts:
            account_number = account.get("account_number") or account.get("number")
            if account_number is not None and str(account_number) == source_num:
                return account
    return None


def build_source_account_info(
    *,
    task_payload: dict[str, Any],
    snapshot: dict[str, Any],
    accounts: list[dict[str, Any]],
    locale: str,
) -> str | None:
    """Build localized source-account line from task/snapshot data."""
    bank = snapshot.get("sourceBank") or snapshot.get("source_bank") or task_payload.get("source_bank_name")
    account_number = (
        snapshot.get("sourceAccount") or snapshot.get("source_account") or task_payload.get("source_account_number")
    )
    source_account = _resolve_source_account(
        accounts=accounts,
        source_account_id=str(task_payload.get("source_account_id")) if task_payload.get("source_account_id") else None,
        source_account_number=str(account_number) if account_number else None,
    )
    if source_account:
        bank = bank or source_account.get("bank_name") or source_account.get("bank")
        account_number = account_number or _account_number_or_last4(source_account)

    if not bank or not account_number:
        return None

    balance = _extract_cached_balance(source_account)
    return format_source_account_info_from_account_number(
        bank=str(bank),
        account_number=str(account_number),
        locale=locale,
        balance=balance,
    )


def _source_line_prefix(locale: str) -> str:
    template = render_message(
        "orchestrator.execution.source_account_info",
        locale,
        {"bank": "", "last4": ""},
    )
    return template.split("(···", maxsplit=1)[0].strip()


def _has_source_line(summary: str, locale: str) -> bool:
    prefix = _source_line_prefix(locale)
    if not prefix:
        return False
    return any(line.strip().startswith(prefix) for line in summary.splitlines())


def append_source_account_info(summary: str, source_account_info: str | None, *, locale: str) -> str:
    """Append source line exactly once."""
    if not source_account_info:
        return summary
    if source_account_info in summary or _has_source_line(summary, locale):
        return summary
    if not summary:
        return source_account_info
    return f"{summary}\n\n{source_account_info}"


def strip_source_account_info_lines(summary: str, *, locale: str) -> str:
    """Remove source-account lines from a summary while preserving readable spacing."""
    if not summary:
        return summary

    prefix = _source_line_prefix(locale)
    if not prefix:
        return summary.strip()

    kept_lines = [line for line in summary.splitlines() if not line.strip().startswith(prefix)]
    compacted_lines: list[str] = []
    last_blank = False
    for line in kept_lines:
        is_blank = not line.strip()
        if is_blank and last_blank:
            continue
        compacted_lines.append(line)
        last_blank = is_blank

    return "\n".join(compacted_lines).strip()


def build_confirmation_summary(
    *,
    task_payload: dict[str, Any],
    locale: str,
    accounts: list[dict[str, Any]],
) -> str | None:
    """Build canonical confirmation summary with optional source-account line."""
    confirmation_payload = task_payload.get("confirmation") or {}
    summary = confirmation_payload.get("summary")
    if not isinstance(summary, str) or not summary:
        return None

    funding_plan = task_payload.get("funding_plan")
    if isinstance(funding_plan, dict) and not funding_plan.get("is_single_source", True):
        return summary

    snapshot = confirmation_payload.get("snapshot")
    snapshot_mapping = snapshot if isinstance(snapshot, dict) else {}
    source_account_info = build_source_account_info(
        task_payload=task_payload,
        snapshot=snapshot_mapping,
        accounts=accounts,
        locale=locale,
    )
    return append_source_account_info(summary, source_account_info, locale=locale)
