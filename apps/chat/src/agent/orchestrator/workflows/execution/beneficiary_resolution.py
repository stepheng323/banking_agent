"""Beneficiary lookup helpers for execution handlers."""

from __future__ import annotations

import re
from typing import Any

from shared.utils.serialization import sqlalchemy_to_dict

_RECIPIENT_PRONOUN_TOKENS = {"her", "him", "them", "that", "it", "this", "previous"}


def _normalize_beneficiary_rows(rows: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            record = dict(row)
        else:
            record = sqlalchemy_to_dict(row)
            account_number = getattr(row, "account_number", None)
            if account_number is not None:
                record["account_number"] = str(account_number)
        normalized.append(record)
    return normalized


def _recipient_supports_targeted_beneficiary_lookup(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower()
    if not normalized:
        return False
    return normalized not in _RECIPIENT_PRONOUN_TOKENS


def _normalize_beneficiary_match_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.strip().lower()).strip()


def _beneficiary_cache_contains_recipient(beneficiaries: list[dict[str, Any]], recipient_name: str) -> bool:
    requested = _normalize_beneficiary_match_text(recipient_name)
    if not requested:
        return False

    for beneficiary in beneficiaries:
        alias = _normalize_beneficiary_match_text(beneficiary.get("alias"))
        account_name = _normalize_beneficiary_match_text(beneficiary.get("account_name"))
        if alias and (requested in alias or alias in requested):
            return True
        if account_name and (requested in account_name or account_name in requested):
            return True
    return False


__all__ = [
    "_beneficiary_cache_contains_recipient",
    "_normalize_beneficiary_rows",
    "_recipient_supports_targeted_beneficiary_lookup",
]
