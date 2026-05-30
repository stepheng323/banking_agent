"""Modifier and source-account helpers for quoted replay."""

import re
from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.workers.__shared__.account_selection.reference import (
    build_source_account_patch,
    match_source_account_reference,
)
from shared.money import MoneyAmount
from shared.types.planner import ContextFrameReplayModifier

QUOTED_REPLAY_MODIFIER_MIN_CONFIDENCE = 0.72


def _contains_replay_modifier_evidence(text: str | None, evidence: str | None) -> bool:
    if not text or not evidence:
        return False
    normalized_text = re.sub(r"\s+", " ", text).strip().casefold()
    normalized_evidence = re.sub(r"\s+", " ", evidence).strip().casefold()
    return bool(normalized_evidence and normalized_evidence in normalized_text)


def _trusted_replay_modifier(modifier: ContextFrameReplayModifier | None) -> ContextFrameReplayModifier | None:
    if modifier is None:
        return None
    if modifier.confidence < QUOTED_REPLAY_MODIFIER_MIN_CONFIDENCE:
        return None
    return modifier


def _modifier_amount_override(text: str | None, modifier: ContextFrameReplayModifier | None) -> MoneyAmount | None:
    trusted = _trusted_replay_modifier(modifier)
    if trusted is None or trusted.amount is None:
        return None
    if not _contains_replay_modifier_evidence(text, trusted.amount_evidence):
        return None
    return trusted.amount if trusted.amount > 0 else None


def _modifier_source_account_candidate(text: str | None, modifier: ContextFrameReplayModifier | None) -> str | None:
    trusted = _trusted_replay_modifier(modifier)
    if trusted is None or not trusted.source_account_reference:
        return None
    if not _contains_replay_modifier_evidence(text, trusted.source_account_evidence):
        return None
    return trusted.source_account_reference.strip() or None


def _modifier_narration_candidate(text: str | None, modifier: ContextFrameReplayModifier | None) -> str | None:
    trusted = _trusted_replay_modifier(modifier)
    if trusted is None or not trusted.narration:
        return None
    if not _contains_replay_modifier_evidence(text, trusted.narration_evidence):
        return None
    return trusted.narration.strip() or None


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
            account_id = str(
                account.get("id") or account.get("account_id") or account.get("source_account_id") or ""
            ).strip()
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


def _quoted_replay_source_override(
    state: OrchestratorState,
    text: str,
    replay_modifier: ContextFrameReplayModifier | None,
) -> tuple[bool, dict[str, Any] | None, str | None]:
    candidate = _modifier_source_account_candidate(text, replay_modifier)
    if not candidate:
        return False, None, None

    matched_account = match_source_account_reference(candidate, _loaded_accounts(state))
    if not matched_account:
        return True, None, candidate

    return True, build_source_account_patch(matched_account), candidate


__all__ = [
    "QUOTED_REPLAY_MODIFIER_MIN_CONFIDENCE",
    "_modifier_amount_override",
    "_modifier_narration_candidate",
    "_modifier_source_account_candidate",
    "_quoted_replay_source_override",
]
