"""Source-account reference resolution for pending-action edits."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.pending_action.context.pending_action_account_context import (
    _loaded_accounts,
)


def _normalize_account_reference(value: Any) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def _resolve_source_account_by_bank(
    state: OrchestratorState,
    source_bank_name: Any,
) -> dict[str, Any] | None:
    normalized_source_bank = _normalize_account_reference(source_bank_name)
    if not normalized_source_bank:
        return None
    matches = []
    for account in _loaded_accounts(state):
        bank_name = account.get("bank_name") or account.get("bank") or account.get("source_bank_name")
        normalized_bank_name = _normalize_account_reference(bank_name)
        if not normalized_bank_name:
            continue
        if (
            normalized_bank_name == normalized_source_bank
            or normalized_bank_name in normalized_source_bank
            or normalized_source_bank in normalized_bank_name
        ):
            matches.append(account)
    return matches[0] if len(matches) == 1 else None


def _resolve_source_account_by_index(
    state: OrchestratorState,
    source_account_index: Any,
) -> dict[str, Any] | None:
    try:
        index = int(source_account_index)
    except (TypeError, ValueError):
        return None
    if index <= 0:
        return None
    accounts = _loaded_accounts(state)
    if index > len(accounts):
        return None
    return accounts[index - 1]


def _resolve_source_bank_name_from_account_reference(
    *,
    state: OrchestratorState,
    decision: Any,
    text: str,
) -> str | None:
    references = [
        text,
        *(str(item or "") for item in getattr(decision, "target_texts", []) or []),
        str(getattr(decision, "reason", None) or ""),
    ]
    normalized_references = [_normalize_account_reference(item) for item in references if item]
    if not normalized_references:
        return None

    matches: list[str] = []
    for account in _loaded_accounts(state):
        bank_name = str(
            account.get("bank_name") or account.get("bank") or account.get("source_bank_name") or ""
        ).strip()
        normalized_bank_name = _normalize_account_reference(bank_name)
        if normalized_bank_name and any(normalized_bank_name in reference for reference in normalized_references):
            matches.append(bank_name)
    unique_matches = {match for match in matches if match}
    return next(iter(unique_matches)) if len(matches) == 1 and len(unique_matches) == 1 else None


__all__ = [
    "_normalize_account_reference",
    "_resolve_source_account_by_bank",
    "_resolve_source_account_by_index",
    "_resolve_source_bank_name_from_account_reference",
]
