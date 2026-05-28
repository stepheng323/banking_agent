"""Source-account overrides for context-frame replay modifiers."""

import re
from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_replay_modifier_core import (
    _modifier_source_account_candidate,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_frame_replay_modifier_text import (
    _clean_replay_modifier_text,
)
from apps.chat.src.agent.workers.__shared__.account_selection.reference import match_source_account_reference
from shared.types.planner import ContextFrameReplayModifier

_REPLAY_SOURCE_ACCOUNT_RE = re.compile(
    r"\b(?:from|using|use|debit|charge|switch(?:\s+it)?\s+to|"
    r"change\s+source(?:\s+account)?\s+to|"
    r"source(?:\s+account)?(?:\s+as|\s+to|\s+is)?|"
    r"with(?:\s+my|\s+the)|"
    r"make\s+(?:e|am|it)\s+(?:from|use)|"
    r"lati(?:\s+inu)?|lo|daga|(?:yi\s+)?amfani\s+da|ta\s+hanyar|site\s+na|jiri)\s+"
    r"(?:my\s+|the\s+)?"
    r"(?P<source>[a-z0-9][a-z0-9 .&'()-]{0,80}?)"
    r"(?=\s+(?:instead|for|fun|domin|saboda|maka|with|narration|memo|note|"
    r"description|reason|akosile|bayani|bayanin|nkowa|and|but)\b|[.?!,;]|$)",
    re.IGNORECASE,
)


def _replay_source_account_candidate(text: str | None) -> str | None:
    if not text:
        return None

    for match in _REPLAY_SOURCE_ACCOUNT_RE.finditer(text):
        candidate = _clean_replay_modifier_text(match.group("source"))
        if candidate:
            return candidate
    return None


def _loaded_accounts(state: OrchestratorState) -> list[dict[str, Any]]:
    loaded_context = state.loaded_context or {}
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
            account_id = str(account.get("id") or account.get("account_id") or "").strip()
            bank_name = str(
                account.get("bank_name")
                or account.get("bank")
                or account.get("source_bank_name")
                or ""
            ).strip()
            account_number = str(
                account.get("account_number")
                or account.get("source_account_number")
                or ""
            ).strip()
            key = account_id or f"{bank_name}:{account_number}"
            if key and key not in accounts_by_key:
                accounts_by_key[key] = account
    return list(accounts_by_key.values())


def _source_account_patch(account: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_account_id": account.get("id") or account.get("account_id"),
        "source_bank_name": account.get("bank_name") or account.get("bank"),
        "source_account_name": account.get("account_name") or account.get("name"),
        "source_account_number": account.get("account_number") or account.get("source_account_number"),
        "source_affinity_mode": "explicit",
        "source_account_index": None,
        "funding_plan": None,
        "suggested_amount": None,
    }


def _replay_source_account_override(
    text: str | None,
    state: OrchestratorState,
    replay_modifier: ContextFrameReplayModifier | None,
) -> tuple[bool, dict[str, Any] | None, str | None]:
    candidate = _replay_source_account_candidate(text)
    if not candidate:
        candidate = _modifier_source_account_candidate(text, replay_modifier)
    if not candidate:
        return False, None, None

    matched_account = match_source_account_reference(candidate, _loaded_accounts(state))
    if not matched_account:
        return True, None, candidate

    return True, _source_account_patch(matched_account), candidate


__all__ = [
    "_loaded_accounts",
    "_replay_source_account_candidate",
    "_replay_source_account_override",
    "_source_account_patch",
]
