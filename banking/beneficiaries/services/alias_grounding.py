"""Deterministic grounding for exact saved-beneficiary aliases."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


def _normalize_alias_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", without_marks.casefold()).strip()


def restore_exact_saved_alias(
    message: str | None,
    extracted_recipient_name: str | None,
    beneficiaries: list[dict[str, Any]],
) -> str | None:
    """Restore a full saved alias when extraction retained only its leading name.

    This is entity grounding, not intent recognition. The caller has already
    established that the turn is a transfer. We only accept aliases that occur
    as a complete token span in the raw message, and only when the extracted
    recipient is the full alias or a leading token sequence of it. Longest-match
    selection prevents a shorter alias such as ``Tolu`` from eclipsing the more
    specific ``Tolu Access``.

    The returned alias is not an authorization or beneficiary selection. The
    transfer resolver must still validate the saved record and handle duplicate
    or stale destinations.
    """

    normalized_message = _normalize_alias_text(message)
    normalized_recipient = _normalize_alias_text(extracted_recipient_name)
    if not normalized_message or not normalized_recipient:
        return None

    padded_message = f" {normalized_message} "
    matches: dict[str, str] = {}
    for beneficiary in beneficiaries:
        alias = beneficiary.get("alias")
        normalized_alias = _normalize_alias_text(alias)
        if not normalized_alias or f" {normalized_alias} " not in padded_message:
            continue
        if not (normalized_recipient == normalized_alias or normalized_alias.startswith(f"{normalized_recipient} ")):
            continue
        if isinstance(alias, str) and alias.strip():
            matches.setdefault(normalized_alias, alias.strip())

    if not matches:
        return None

    longest_token_count = max(len(alias.split()) for alias in matches)
    longest = [
        raw_alias
        for normalized_alias, raw_alias in matches.items()
        if len(normalized_alias.split()) == longest_token_count
    ]
    if len(longest) != 1:
        return None
    return longest[0]


__all__ = ["restore_exact_saved_alias"]
