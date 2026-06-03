"""Source-account enrichment for context-frame transaction replay payloads."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_replay_accounts import (
    _loaded_accounts,
)


def enrich_replay_source_account(payload: dict[str, Any], state: OrchestratorState) -> None:
    if payload.get("source_account_number"):
        return

    source_account_id = str(payload.get("source_account_id") or "").strip()
    source_bank_name = str(payload.get("source_bank_name") or "").strip().casefold()
    matched_account: dict[str, Any] | None = None
    for account in _loaded_accounts(state):
        account_id = str(
            account.get("id") or account.get("account_id") or account.get("source_account_id") or ""
        ).strip()
        bank_name = (
            str(account.get("bank_name") or account.get("bank") or account.get("source_bank_name") or "")
            .strip()
            .casefold()
        )
        if source_account_id and account_id == source_account_id:
            matched_account = account
            break
        if source_bank_name and bank_name == source_bank_name:
            matched_account = account
            break

    if not matched_account:
        return

    if not payload.get("source_account_id"):
        source_id = (
            matched_account.get("id") or matched_account.get("account_id") or matched_account.get("source_account_id")
        )
        if source_id:
            payload["source_account_id"] = source_id
    if not payload.get("source_bank_name"):
        matched_bank_name = (
            matched_account.get("bank_name") or matched_account.get("bank") or matched_account.get("source_bank_name")
        )
        if matched_bank_name:
            payload["source_bank_name"] = matched_bank_name
    account_number = (
        matched_account.get("account_number")
        or matched_account.get("number")
        or matched_account.get("source_account_number")
    )
    if account_number:
        payload["source_account_number"] = account_number


__all__ = ["enrich_replay_source_account"]
