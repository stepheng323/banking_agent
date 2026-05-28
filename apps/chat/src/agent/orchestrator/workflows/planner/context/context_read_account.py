"""Deterministic account responses for planner context reads."""

import re
from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from shared.i18n.renderer import render_message
from shared.services.onboarding.mandate_messages import build_pending_mandate_message
from shared.utils.bank_aliases import BANK_ALIASES, get_bank_search_terms, normalize_bank_name

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _compact_token(value: str) -> str:
    return _NON_ALNUM_RE.sub("", value.lower())


def _find_account_for_bank_followup(text: str, accounts: list[dict[str, Any]]) -> dict[str, Any] | None:
    compact_text = _compact_token(text)
    if not compact_text:
        return None

    for account in accounts:
        bank_name = str(account.get("bank_name") or "").strip()
        if not bank_name:
            continue
        search_terms = {normalize_bank_name(bank_name), *get_bank_search_terms(bank_name)}
        if any(term and _compact_token(term) in compact_text for term in search_terms):
            return account
    return None


def _extract_requested_bank_label(text: str) -> str | None:
    lowered = text.lower()
    for alias in sorted(BANK_ALIASES.keys(), key=len, reverse=True):
        if alias in lowered:
            return alias.title()
    return None


def synthesize_account_context_read_response(
    state: OrchestratorState,
    subtype: str,
    text: str,
    locale: str,
) -> str | None:
    """Build deterministic account context-read responses from loaded context when beneficial."""
    accounts_raw = (state.loaded_context or {}).get("accounts")
    accounts = accounts_raw if isinstance(accounts_raw, list) else []
    if subtype != "account_linked_bank_existence_check" or not accounts:
        return None

    match = _find_account_for_bank_followup(text, [a for a in accounts if isinstance(a, dict)])
    if match is None:
        bank_label = _extract_requested_bank_label(text)
        if bank_label:
            return f"No, you do not have {bank_label} linked."
        return None

    bank_name = str(match.get("bank_name") or render_message("mandate.bank_fallback", locale))
    status = str(match.get("mandate_status") or "").strip().lower()
    if status == "ready":
        return f"Yes, you have {bank_name} linked and ready."

    pending_message = build_pending_mandate_message([match], locale)
    return f"Yes, you have {bank_name} linked, but it is not ready for payments yet.\n\n{pending_message}"


__all__ = ["synthesize_account_context_read_response"]
