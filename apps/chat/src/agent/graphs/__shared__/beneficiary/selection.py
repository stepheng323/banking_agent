"""Helpers for deterministic beneficiary option selection."""

import re
from typing import Any

_BENEFICIARY_SELECTION_STOPWORDS = {
    "the",
    "bank",
    "acct",
    "account",
    "please",
    "pls",
}
_BENEFICIARY_SELECTION_ALIASES = {
    "gtbank": "gtb",
    "gt": "gtb",
    "guarantytrust": "gtb",
    "guarantytrustbank": "gtb",
    "accessbank": "access",
    "firstbank": "first",
    "fbn": "first",
}


def _beneficiary_selection_tokens(value: str) -> set[str]:
    tokens = {
        token
        for token in re.sub(r"[^a-z0-9]+", " ", value.lower()).split()
        if token and token not in _BENEFICIARY_SELECTION_STOPWORDS
    }
    expanded = set(tokens)
    for token in tokens:
        alias = _BENEFICIARY_SELECTION_ALIASES.get(token)
        if alias:
            expanded.add(alias)
        if token.endswith("bank") and len(token) > 4:
            expanded.add(token[:-4])
    return expanded


def match_beneficiary_candidate_selection(
    user_text: str,
    candidates: list[dict[str, Any]],
) -> str | None:
    """Return the selected beneficiary id when user text matches exactly one option."""
    input_tokens = _beneficiary_selection_tokens(user_text)
    if not input_tokens:
        return None

    matches: list[str] = []
    for candidate in candidates:
        beneficiary_id = str(candidate.get("beneficiary_id", "")).strip()
        if not beneficiary_id:
            continue
        label = str(candidate.get("label", ""))
        option_title = str(candidate.get("title", ""))
        candidate_tokens = _beneficiary_selection_tokens(f"{label} {option_title}")
        if input_tokens.issubset(candidate_tokens):
            matches.append(beneficiary_id)

    unique_matches = list(dict.fromkeys(matches))
    if len(unique_matches) == 1:
        return unique_matches[0]
    return None
