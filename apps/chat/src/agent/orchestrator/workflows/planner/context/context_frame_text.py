"""Text and value parsing helpers for context-frame follow-ups."""

import re
from decimal import Decimal, InvalidOperation

from banking.presentation.formatters.currency import format_naira_compact
from shared.money import MoneyAmount, to_money

LOOKUP_STOPWORDS = {
    "a",
    "about",
    "all",
    "am",
    "an",
    "and",
    "any",
    "are",
    "bank",
    "beneficiaries",
    "beneficiary",
    "check",
    "did",
    "do",
    "does",
    "else",
    "for",
    "have",
    "how",
    "i",
    "is",
    "it",
    "list",
    "more",
    "my",
    "of",
    "one",
    "other",
    "recipients",
    "saved",
    "show",
    "still",
    "that",
    "the",
    "then",
    "this",
    "those",
    "view",
    "what",
    "with",
    "you",
}
_TOKEN_ALIASES: dict[str, tuple[str, ...]] = {
    "gt": ("gtbank", "gt bank"),
    "gtb": ("gtbank",),
    "gtbank": ("gt bank", "gtb"),
}


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower()).rstrip("?.!,")


def lookup_tokens(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [token for token in tokens if len(token) > 1 and token not in LOOKUP_STOPWORDS]


def _token_variants(token: str) -> tuple[str, ...]:
    return (token, *_TOKEN_ALIASES.get(token, ()))


def token_matches_searchable(token: str, searchable: str) -> bool:
    return any(re.search(rf"\b{re.escape(variant)}\b", searchable) for variant in _token_variants(token))


def semantic_tokens(text: str | None) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(token) > 1}


def amount_reference_values(text: str | None) -> set[MoneyAmount]:
    if not text:
        return set()

    values: set[MoneyAmount] = set()
    normalized = text.lower().replace(",", "")

    def _add(raw_number: str, suffix: str | None = None) -> None:
        try:
            value = Decimal(raw_number)
        except InvalidOperation:
            return
        if suffix == "k":
            value *= Decimal("1000")
        elif suffix == "m":
            value *= Decimal("1000000")
        amount = to_money(value)
        if amount is not None:
            values.add(amount)

    for match in re.finditer(r"(?:₦|ngn|naira)\s*([0-9]+(?:\.[0-9]+)?)\s*([km])?\b", normalized):
        _add(match.group(1), match.group(2))

    for match in re.finditer(r"\b([0-9]+(?:\.[0-9]+)?)\s*([km])\b", normalized):
        _add(match.group(1), match.group(2))

    for match in re.finditer(r"\b[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?\b", text.lower()):
        _add(match.group(0).replace(",", ""))

    for match in re.finditer(r"\b[0-9]{4,}(?:\.[0-9]+)?\b", normalized):
        _add(match.group(0))

    return values


def format_currency_amount(value: MoneyAmount) -> str:
    return format_naira_compact(value, absolute=True)


__all__ = [
    "LOOKUP_STOPWORDS",
    "amount_reference_values",
    "format_currency_amount",
    "lookup_tokens",
    "normalize",
    "semantic_tokens",
    "token_matches_searchable",
]
