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


def _selection_line_candidates(user_text: str) -> list[str]:
    lines = [
        re.sub(r"^\s*(?:\d+[\).:-]?|[•*\-]+)\s*", "", line).strip() for line in user_text.splitlines() if line.strip()
    ]
    candidates: list[str] = []
    for line in lines:
        tokens = [
            token
            for token in re.sub(r"[^a-z0-9]+", " ", line.lower()).split()
            if token and token not in _BENEFICIARY_SELECTION_STOPWORDS
        ]
        if len(tokens) < 2:
            continue
        candidates.append(line)
        # Users often paste a candidate row with typoed trailing bank text, e.g.
        # "Tolu Adeyemi • GTBan". The name prefix is enough when it is unique.
        for keep_count in range(len(tokens) - 1, 1, -1):
            candidates.append(" ".join(tokens[:keep_count]))
    return candidates


def _match_tokens_to_candidates(input_tokens: set[str], candidates: list[dict[str, Any]]) -> str | None:
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


def match_beneficiary_candidate_selection(
    user_text: str,
    candidates: list[dict[str, Any]],
) -> str | None:
    """Return the selected beneficiary id when user text matches exactly one option."""
    if selected := _match_tokens_to_candidates(_beneficiary_selection_tokens(user_text), candidates):
        return selected
    line_matches = [
        selected
        for candidate_line in _selection_line_candidates(user_text)
        if (selected := _match_tokens_to_candidates(_beneficiary_selection_tokens(candidate_line), candidates))
    ]
    unique_line_matches = list(dict.fromkeys(line_matches))
    if len(unique_line_matches) == 1:
        return unique_line_matches[0]
    return None
