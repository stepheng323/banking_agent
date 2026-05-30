"""Lexical extraction helpers for planner task normalization."""

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from shared.money import MoneyAmount, to_naira
from shared.utils.bank_aliases import BANK_ALIASES, normalize_bank_name
from shared.utils.network_utils import normalize_network_name, normalize_nigerian_phone
from shared.utils.sanitize import normalize_bank_account_number

_PHONE_PATTERN = re.compile(r"(?:\+?234|0)?(?:[\s().-]*\d){10,13}")
_ACCOUNT_PATTERN = re.compile(r"(?:\d[\s,.\-]?){10,11}")
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")
_AMOUNT_TOKEN_PATTERN = re.compile(
    r"(?<!\d)(?:₦|ngn)?\s*(?P<number>\d[\d,]*(?:\.\d+)?)(?P<suffix>[kKmMhH]?)(?!\d)"
)
DATA_PLAN_PATTERN = re.compile(r"(?<!\d)(\d{1,3}(?:\.\d+)?)\s*(gb|mb)(?!\w)", re.IGNORECASE)
BALANCE_SHARE_PERCENT_PATTERN = re.compile(r"\b(?P<pct>\d{1,3})\s*%\b", re.IGNORECASE)

_CANONICAL_NETWORKS = {"MTN", "AIRTEL", "GLO", "9MOBILE"}
_CANONICAL_BANK_DISPLAY = {
    "gtbank": "GTBank",
    "uba": "UBA",
    "firstbank": "First Bank",
    "fcmb": "FCMB",
    "stanbic": "Stanbic",
    "ecobank": "Ecobank",
    "fidelity": "Fidelity",
    "wema": "Wema",
    "polaris": "Polaris",
    "keystone": "Keystone",
    "union": "Union",
    "sterling": "Sterling",
    "providus": "Providus",
    "opay": "Opay",
    "palmpay": "PalmPay",
    "kuda": "Kuda",
    "moniepoint": "Moniepoint",
    "access": "Access Bank",
}
_BANK_ALIASES = sorted(
    {alias for alias in (list(BANK_ALIASES.keys()) + list(BANK_ALIASES.values())) if alias and len(alias) >= 3},
    key=len,
    reverse=True,
)


def digits_only(value: str) -> str:
    return re.sub(r"\D+", "", value or "")


def extract_account_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for raw in _ACCOUNT_PATTERN.findall(text):
        normalized = normalize_bank_account_number(raw)
        if not normalized:
            continue
        if len(normalized) not in {10, 11}:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(normalized)
    return candidates


def extract_phone_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for raw in _PHONE_PATTERN.findall(text):
        normalized = normalize_nigerian_phone(raw)
        if not normalized:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(normalized)
    return candidates


def extract_network_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for token in _TOKEN_PATTERN.findall(text):
        normalized = normalize_network_name(token)
        if not normalized and token.strip().upper() in _CANONICAL_NETWORKS:
            normalized = token.strip().upper()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(normalized)
    return candidates


def extract_amount_candidates(text: str) -> list[MoneyAmount]:
    candidates: list[MoneyAmount] = []
    seen: set[MoneyAmount] = set()
    for match in _AMOUNT_TOKEN_PATTERN.finditer(text):
        num_text = match.group("number").replace(",", "")
        suffix = (match.group("suffix") or "").lower()
        try:
            numeric = Decimal(num_text)
        except InvalidOperation:
            continue
        if numeric <= 0:
            continue

        # Precision-first: avoid interpreting long account numbers as amount.
        digits = digits_only(num_text)
        has_currency_or_suffix = "₦" in match.group(0) or "ngn" in match.group(0).lower() or bool(suffix)
        if not has_currency_or_suffix and len(digits) >= 9:
            continue

        multiplier = Decimal("1")
        if suffix == "k":
            multiplier = Decimal("1000")
        elif suffix == "h":
            multiplier = Decimal("100")
        elif suffix == "m":
            multiplier = Decimal("1000000")

        amount = to_naira(numeric * multiplier)
        if amount is None:
            continue
        if amount in seen:
            continue
        seen.add(amount)
        candidates.append(amount)
    return candidates


def extract_data_plan_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for match in DATA_PLAN_PATTERN.finditer(text):
        amount = match.group(1)
        unit = match.group(2).upper()
        value = f"{amount}{unit}"
        if value in seen:
            continue
        seen.add(value)
        candidates.append(value)
    return candidates


def extract_bank_candidates(text: str) -> list[str]:
    lowered = text.lower()
    canonical_hits: dict[str, str] = {}
    for alias in _BANK_ALIASES:
        pattern = r"\b" + re.escape(alias).replace("\\ ", r"\s+") + r"\b"
        if not re.search(pattern, lowered):
            continue
        canonical = normalize_bank_name(alias)
        if canonical in canonical_hits:
            continue
        canonical_hits[canonical] = alias

    results: list[str] = []
    for canonical, alias in canonical_hits.items():
        display = _CANONICAL_BANK_DISPLAY.get(canonical)
        if display:
            results.append(display)
        else:
            results.append(alias.title())
    return results


def single_unambiguous(values: list[Any]) -> tuple[Any | None, bool]:
    """Return (value, ambiguous)."""
    if not values:
        return None, False
    if len(values) > 1:
        return None, True
    return values[0], False


def parse_amount_value(value: str | MoneyAmount | None) -> MoneyAmount | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    raw = str(value).strip()
    if not raw:
        return None
    single, ambiguous = single_unambiguous(extract_amount_candidates(raw))
    if ambiguous:
        return None
    if isinstance(single, Decimal):
        return single
    return to_naira(raw)
