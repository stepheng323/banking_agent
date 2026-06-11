"""Deterministic source-account reference matching."""

import re
from typing import Any

from shared.utils.bank_aliases import get_bank_search_terms, normalize_bank_name

_ACCOUNT_SELECTION_STOPWORDS = {
    "the",
    "my",
    "bank",
    "acct",
    "account",
    "from",
    "use",
    "using",
    "please",
    "pls",
}


def _account_selection_tokens(value: str) -> set[str]:
    raw_tokens = [
        token
        for token in re.sub(r"[^a-z0-9]+", " ", value.lower()).split()
        if token and token not in _ACCOUNT_SELECTION_STOPWORDS
    ]
    expanded = set(raw_tokens)
    for token in raw_tokens:
        normalized = normalize_bank_name(token)
        if normalized:
            expanded.add(normalized)
            expanded.update(term.replace(" ", "") for term in get_bank_search_terms(normalized))
        if token.endswith("bank") and len(token) > 4:
            expanded.add(token[:-4])
    return expanded


def _tokens_for_account(account: dict[str, Any]) -> set[str]:
    bank_name = str(account.get("bank_name") or account.get("bank") or account.get("source_bank_name") or "")
    account_name = str(account.get("account_name") or account.get("name") or account.get("source_account_name") or "")
    account_number = str(account.get("account_number") or account.get("source_account_number") or "")
    account_id = str(account.get("id") or account.get("account_id") or account.get("source_account_id") or "")
    candidate_text = f"{bank_name} {account_name} {account_number} {account_id}"
    tokens = _account_selection_tokens(candidate_text)
    if account_number:
        tokens.add(account_number)
        if len(account_number) >= 4:
            tokens.add(account_number[-4:])
    return tokens


def match_source_account_reference(
    user_text: str,
    accounts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Return the selected account when user text matches exactly one account."""
    input_tokens = _account_selection_tokens(user_text)
    if not input_tokens:
        return None

    matches: list[dict[str, Any]] = []
    for account in accounts:
        if input_tokens.issubset(_tokens_for_account(account)):
            matches.append(account)

    unique: dict[str, dict[str, Any]] = {}
    for account in matches:
        account_id = str(account.get("id") or account.get("account_id") or "").strip()
        bank_name = account.get("bank_name") or account.get("bank") or account.get("source_bank_name")
        account_number = account.get("account_number") or account.get("source_account_number")
        key = account_id or f"{bank_name}:{account_number}"
        unique[key] = account

    if len(unique) == 1:
        return next(iter(unique.values()))
    return None


def build_source_account_patch(account: dict[str, Any]) -> dict[str, Any]:
    account_id = account.get("id") or account.get("account_id") or account.get("source_account_id")
    bank_name = account.get("bank_name") or account.get("bank") or account.get("source_bank_name")
    account_name = account.get("account_name") or account.get("name") or account.get("source_account_name")
    account_number = source_account_display_number(account)
    return {
        "source_account_id": str(account_id).strip() if account_id else None,
        "source_bank_name": bank_name,
        "source_account_name": account_name,
        "source_account_number": account_number,
        "source_affinity_mode": "explicit",
        "source_account_index": None,
        "funding_plan": None,
        "confirmation": {"confirmed": False},
    }


def source_account_display_number(account: dict[str, Any]) -> str | None:
    account_number = account.get("account_number") or account.get("number") or account.get("source_account_number")
    if account_number:
        return str(account_number)
    last4 = account.get("account_number_last4") or account.get("last4") or account.get("source_account_number_last4")
    return str(last4) if last4 else None
