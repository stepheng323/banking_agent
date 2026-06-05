"""Loaded-account context helpers for pending-action edits."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view


def _loaded_accounts(state: OrchestratorState) -> list[dict[str, Any]]:
    loaded_context = interrupt_state_view(state).loaded_context_or_empty
    account_sources = (
        loaded_context.get("transaction_accounts"),
        loaded_context.get("accounts"),
        loaded_context.get("all_accounts"),
    )
    accounts_by_key: dict[str, dict[str, Any]] = {}
    for account_source in account_sources:
        if not isinstance(account_source, list):
            continue
        for account in account_source:
            if not isinstance(account, dict):
                continue
            account_id = str(
                account.get("id") or account.get("account_id") or account.get("source_account_id") or ""
            ).strip()
            bank_name = str(
                account.get("bank_name") or account.get("bank") or account.get("source_bank_name") or ""
            ).strip()
            account_number = str(
                account.get("account_number") or account.get("number") or account.get("source_account_number") or ""
            ).strip()
            key = account_id or f"{bank_name}:{account_number}"
            if key and key not in accounts_by_key:
                accounts_by_key[key] = account
    return list(accounts_by_key.values())


__all__ = ["_loaded_accounts"]
