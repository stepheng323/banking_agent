"""Source-account patch builders for pending-action edits."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState

from ..context.pending_action_account_context import _loaded_accounts
from ..payloads.pending_action_source_account_resolution import (
    _resolve_source_account_by_bank,
    _resolve_source_account_by_index,
)


def _account_source_patch(account: dict[str, Any]) -> dict[str, Any]:
    bank_name = str(account.get("bank_name") or account.get("bank") or account.get("source_bank_name") or "").strip()
    account_number = str(
        account.get("account_number") or account.get("number") or account.get("source_account_number") or ""
    ).strip()
    account_name = str(
        account.get("account_name") or account.get("name") or account.get("source_account_name") or ""
    ).strip()
    account_id = str(account.get("id") or account.get("account_id") or account.get("source_account_id") or "").strip()
    return {
        "confirmation": {"confirmed": False},
        "source_account_id": account_id or None,
        "source_account_name": account_name or None,
        "source_account_number": account_number or None,
        "source_bank_name": bank_name or None,
        "source_account_index": None,
        "source_affinity_mode": "explicit",
        "funding_plan": None,
    }


def _source_account_patch(
    *,
    state: OrchestratorState,
    source_bank_name: Any = None,
    source_account_index: Any = None,
) -> dict[str, Any]:
    patch: dict[str, Any] = {
        "confirmation": {"confirmed": False},
        "source_account_id": None,
        "source_account_name": None,
        "source_account_number": None,
        "source_affinity_mode": "explicit",
        "funding_plan": None,
    }
    if source_bank_name not in (None, ""):
        account = _resolve_source_account_by_bank(state, source_bank_name)
        if account is not None:
            return _account_source_patch(account)
        patch["source_bank_name"] = str(source_bank_name).strip()
        patch["source_account_index"] = None
    if source_account_index not in (None, ""):
        account = _resolve_source_account_by_index(state, source_account_index)
        if account is not None:
            return _account_source_patch(account)
        try:
            index = int(source_account_index)
        except (TypeError, ValueError):
            index = 0
        if index > 0:
            if _loaded_accounts(state):
                return {}
            patch["source_account_index"] = index
            patch["source_bank_name"] = None
    return patch


__all__ = [
    "_account_source_patch",
    "_source_account_patch",
]
