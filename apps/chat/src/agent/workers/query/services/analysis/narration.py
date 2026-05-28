"""Deterministic narration analysis for transaction understanding."""

from __future__ import annotations

import re
from dataclasses import dataclass

from apps.chat.src.agent.workers.query.models.domain import CATEGORY_KEYWORDS, normalize_category

_TRANSFER_FROM_TO_RE = re.compile(
    r"TRANSFER\s+FROM\s+(?P<sender>.+?)\s+TO\s+(?P<recipient>.+)$",
    re.IGNORECASE,
)
_TRANSFER_DIRECTION_RE = re.compile(
    r"(?P<direction>TRANSFER TO|TRANSFER FROM|PAYMENT TO|PAYMENT FROM|FROM|TO)\s+(?P<name>.+)$",
    re.IGNORECASE,
)
_NIP_DIRECTION_RE = re.compile(r"\b(?P<direction>TO|FROM)\s+(?P<name>[A-Z][A-Z\s.&/-]+)$", re.IGNORECASE)
_SLASH_TRANSFER_RE = re.compile(r"^(?:NIP/)?(?P<platform>[A-Z0-9]+)/(?P<name>[A-Z][A-Z\s.&]+)$", re.IGNORECASE)
_SALARY_RE = re.compile(r"^(?:SALARY|PAYROLL)\s+FROM\s+(?P<name>.+)$", re.IGNORECASE)
_INTEREST_RE = re.compile(r"^INTEREST\s+CREDITED", re.IGNORECASE)
_PAYOUT_RE = re.compile(r"^(?P<name>[A-Z0-9 .&/-]+?)\s*-\s*(?:REVENUE\s+)?PAYOUT$", re.IGNORECASE)
_FREELANCE_RE = re.compile(r"^(?P<name>FREELANCE PAYMENT)(?:\s*-\s*(?P<memo>.+))?$", re.IGNORECASE)
_POS_PURCHASE_RE = re.compile(r"^POS PURCHASE\s*-\s*(?P<name>.+)$", re.IGNORECASE)
_WEB_PURCHASE_RE = re.compile(r"^WEB PURCHASE\s*-\s*(?P<name>.+)$", re.IGNORECASE)
_SUBSCRIPTION_RE = re.compile(r"^(?P<name>.+?)\s+(?:MONTHLY\s+)?SUBSCRIPTION$", re.IGNORECASE)
_AIRTIME_RE = re.compile(r"^AIRTIME PURCHASE\s*-\s*(?P<name>.+)$", re.IGNORECASE)
_DATA_RE = re.compile(r"^(?P<name>MTN|GLO|AIRTEL|9MOBILE)\s+DATA BUNDLE", re.IGNORECASE)
_UTILITY_RE = re.compile(r"^(?P<name>IKEDC|EKEDC|PHCN|NEPA|LAWMA)\b", re.IGNORECASE)
_ATM_RE = re.compile(r"^ATM WITHDRAWAL\s*-\s*(?P<name>.+)$", re.IGNORECASE)

_KNOWN_MERCHANTS: dict[str, tuple[str, str]] = {
    "UBER": ("Uber", "transport"),
    "BOLT": ("Bolt", "transport"),
    "TAXIFY": ("Bolt", "transport"),
    "NETFLIX": ("Netflix", "entertainment"),
    "SPOTIFY": ("Spotify", "entertainment"),
    "YOUTUBE": ("YouTube", "entertainment"),
    "SHOPRITE": ("Shoprite", "shopping"),
    "CHICKEN REPUBLIC": ("Chicken Republic", "food"),
    "JUMIA FOOD": ("Jumia Food", "food"),
    "CHOWDECK": ("Chowdeck", "food"),
    "GLOVO": ("Glovo", "food"),
    "COWRYWISE": ("Cowrywise", "savings"),
    "PIGGYVEST": ("Piggyvest", "savings"),
    "PAYSTACK": ("Paystack", "income"),
    "AMAZON PRIME": ("Amazon Prime", "entertainment"),
    "SLOT SYSTEMS": ("Slot Systems", "shopping"),
}
_BANK_CHARGE_TOKENS = ("CHARGE", "FEE", "STAMP DUTY", "VAT", "SMS ALERT", "MAINTENANCE")


@dataclass(frozen=True)
class NarrationAnalysis:
    """Normalized narration analysis used across query execution."""

    counterparty: str | None = None
    counterparty_role: str = "unknown"
    resolved_category: str | None = None
    counterparty_source: str | None = None
    category_source: str | None = None
    parser_rule: str = "unknown"


def _clean_name(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = " ".join(value.strip().split())
    cleaned = re.sub(r"\s*-\s*(?:.+)$", "", cleaned).strip()
    cleaned = re.sub(r"[/|]+$", "", cleaned).strip()
    if not cleaned:
        return None
    return cleaned.title()[:40]


def _choose_category(*candidates: str | None) -> str | None:
    for candidate in candidates:
        normalized = normalize_category(candidate)
        if normalized:
            return normalized
    return None


def _provider_category_or_none(category: str | None) -> str | None:
    normalized = normalize_category(category)
    if normalized in {None, "unknown"}:
        return None
    return normalized


def _analysis(
    *,
    counterparty: str | None,
    counterparty_role: str,
    category: str | None,
    counterparty_source: str,
    category_source: str,
    parser_rule: str,
) -> NarrationAnalysis:
    return NarrationAnalysis(
        counterparty=_clean_name(counterparty),
        counterparty_role=counterparty_role,
        resolved_category=_choose_category(category),
        counterparty_source=counterparty_source,
        category_source=category_source,
        parser_rule=parser_rule,
    )


def _match_known_merchant(narration: str) -> NarrationAnalysis | None:
    upper_narration = narration.upper()
    for token, (display_name, category) in _KNOWN_MERCHANTS.items():
        if token in upper_narration:
            return _analysis(
                counterparty=display_name,
                counterparty_role="merchant",
                category=category,
                counterparty_source="narration",
                category_source="narration_rule",
                parser_rule=f"merchant:{token.lower().replace(' ', '_')}",
            )
    return None


def analyze_transaction_narration(
    *,
    narration: str | None,
    transaction_type: str | None,
    provider_category: str | None = None,
    provider_counterparty: str | None = None,
) -> NarrationAnalysis:
    """Parse narration into counterparty/category signals."""
    raw_narration = " ".join((narration or "").strip().split())
    upper_narration = raw_narration.upper()
    tx_type = (transaction_type or "").strip().lower()
    provider_category_normalized = _provider_category_or_none(provider_category)

    if raw_narration:
        if match := _TRANSFER_FROM_TO_RE.match(upper_narration):
            sender = _clean_name(match.group("sender"))
            recipient = _clean_name(match.group("recipient"))
            if tx_type == "credit":
                return _analysis(
                    counterparty=sender,
                    counterparty_role="sender",
                    category=provider_category_normalized or "transfers",
                    counterparty_source="narration",
                    category_source="provider" if provider_category_normalized else "narration_rule",
                    parser_rule="transfer_from_to_credit",
                )
            return _analysis(
                counterparty=recipient,
                counterparty_role="recipient",
                category=provider_category_normalized or "transfers",
                counterparty_source="narration",
                category_source="provider" if provider_category_normalized else "narration_rule",
                parser_rule="transfer_from_to_debit",
            )

        if "NIP TRANSFER" in upper_narration or upper_narration.startswith("0000"):
            match = _NIP_DIRECTION_RE.search(upper_narration)
            if match:
                direction = match.group("direction").lower()
                role = "sender" if direction == "from" else "recipient"
                return _analysis(
                    counterparty=match.group("name"),
                    counterparty_role=role,
                    category=provider_category_normalized or "transfers",
                    counterparty_source="narration",
                    category_source="provider" if provider_category_normalized else "narration_rule",
                    parser_rule="nip_transfer",
                )

        if match := _TRANSFER_DIRECTION_RE.match(upper_narration):
            direction = match.group("direction").lower()
            role = "sender" if "from" in direction else "recipient"
            return _analysis(
                counterparty=match.group("name"),
                counterparty_role=role,
                category=provider_category_normalized or "transfers",
                counterparty_source="narration",
                category_source="provider" if provider_category_normalized else "narration_rule",
                parser_rule=f"transfer:{direction.replace(' ', '_')}",
            )

        if match := _SLASH_TRANSFER_RE.match(upper_narration):
            role = "sender" if tx_type == "credit" else "recipient"
            return _analysis(
                counterparty=match.group("name"),
                counterparty_role=role,
                category=provider_category_normalized or "transfers",
                counterparty_source="narration",
                category_source="provider" if provider_category_normalized else "narration_rule",
                parser_rule="slash_transfer",
            )

        if match := _SALARY_RE.match(upper_narration):
            return _analysis(
                counterparty=match.group("name"),
                counterparty_role="employer",
                category=provider_category_normalized or "income",
                counterparty_source="narration",
                category_source="provider" if provider_category_normalized else "narration_rule",
                parser_rule="salary",
            )

        if match := _PAYOUT_RE.match(upper_narration):
            return _analysis(
                counterparty=match.group("name"),
                counterparty_role="merchant" if tx_type == "debit" else "sender",
                category=provider_category_normalized or ("income" if tx_type == "credit" else None),
                counterparty_source="narration",
                category_source="provider" if provider_category_normalized else "narration_rule",
                parser_rule="payout",
            )

        if match := _FREELANCE_RE.match(upper_narration):
            return _analysis(
                counterparty=match.group("name"),
                counterparty_role="sender",
                category=provider_category_normalized or "income",
                counterparty_source="narration",
                category_source="provider" if provider_category_normalized else "narration_rule",
                parser_rule="freelance_payment",
            )

        if match := _POS_PURCHASE_RE.match(upper_narration):
            merchant_name = match.group("name")
            known = _match_known_merchant(merchant_name)
            if known is not None:
                return known
            return _analysis(
                counterparty=merchant_name,
                counterparty_role="merchant",
                category=provider_category_normalized or "shopping",
                counterparty_source="narration",
                category_source="provider" if provider_category_normalized else "narration_rule",
                parser_rule="pos_purchase",
            )

        if match := _WEB_PURCHASE_RE.match(upper_narration):
            merchant_name = match.group("name")
            known = _match_known_merchant(merchant_name)
            if known is not None:
                return known
            return _analysis(
                counterparty=merchant_name,
                counterparty_role="merchant",
                category=provider_category_normalized or "shopping",
                counterparty_source="narration",
                category_source="provider" if provider_category_normalized else "narration_rule",
                parser_rule="web_purchase",
            )

        if match := _SUBSCRIPTION_RE.match(upper_narration):
            return _analysis(
                counterparty=match.group("name"),
                counterparty_role="merchant",
                category=provider_category_normalized or "entertainment",
                counterparty_source="narration",
                category_source="provider" if provider_category_normalized else "narration_rule",
                parser_rule="subscription",
            )

        if match := _AIRTIME_RE.match(upper_narration):
            return _analysis(
                counterparty=match.group("name"),
                counterparty_role="merchant",
                category=provider_category_normalized or "airtime",
                counterparty_source="narration",
                category_source="provider" if provider_category_normalized else "narration_rule",
                parser_rule="airtime_purchase",
            )

        if match := _DATA_RE.match(upper_narration):
            return _analysis(
                counterparty=match.group("name"),
                counterparty_role="merchant",
                category=provider_category_normalized or "airtime",
                counterparty_source="narration",
                category_source="provider" if provider_category_normalized else "narration_rule",
                parser_rule="data_bundle",
            )

        if match := _UTILITY_RE.match(upper_narration):
            return _analysis(
                counterparty=match.group("name"),
                counterparty_role="merchant",
                category=provider_category_normalized or "utilities",
                counterparty_source="narration",
                category_source="provider" if provider_category_normalized else "narration_rule",
                parser_rule="utility",
            )

        if any(token in upper_narration for token in _BANK_CHARGE_TOKENS):
            return _analysis(
                counterparty="Bank Charges",
                counterparty_role="bank",
                category=provider_category_normalized or "bank_charges",
                counterparty_source="narration",
                category_source="provider" if provider_category_normalized else "narration_rule",
                parser_rule="bank_charge",
            )

        if match := _ATM_RE.match(upper_narration):
            return _analysis(
                counterparty=match.group("name"),
                counterparty_role="bank",
                category=provider_category_normalized or "cash_withdrawal",
                counterparty_source="narration",
                category_source="provider" if provider_category_normalized else "narration_rule",
                parser_rule="atm",
            )

        if _INTEREST_RE.match(upper_narration):
            return _analysis(
                counterparty="Bank Interest",
                counterparty_role="bank",
                category=provider_category_normalized or "income",
                counterparty_source="narration",
                category_source="provider" if provider_category_normalized else "narration_rule",
                parser_rule="interest",
            )

        known_merchant = _match_known_merchant(upper_narration)
        if known_merchant is not None:
            return known_merchant

        if provider_category_normalized is not None:
            return NarrationAnalysis(
                counterparty=_clean_name(provider_counterparty),
                counterparty_role="unknown",
                resolved_category=provider_category_normalized,
                counterparty_source="provider" if provider_counterparty else None,
                category_source="provider",
                parser_rule="provider_category",
            )

        for category, keywords in CATEGORY_KEYWORDS.items():
            if any(keyword in raw_narration.lower() for keyword in keywords):
                return NarrationAnalysis(
                    counterparty=_clean_name(provider_counterparty),
                    counterparty_role="unknown",
                    resolved_category=category,
                    counterparty_source="provider" if provider_counterparty else None,
                    category_source="narration_rule",
                    parser_rule="category_keyword",
                )

    provider_counterparty_clean = _clean_name(provider_counterparty)
    if provider_counterparty_clean:
        return NarrationAnalysis(
            counterparty=provider_counterparty_clean,
            counterparty_role="unknown",
            resolved_category=provider_category_normalized,
            counterparty_source="provider",
            category_source="provider" if provider_category_normalized else None,
            parser_rule="provider_counterparty",
        )

    return NarrationAnalysis(
        counterparty=None,
        counterparty_role="unknown",
        resolved_category=provider_category_normalized,
        counterparty_source=None,
        category_source="provider" if provider_category_normalized else None,
        parser_rule="unknown",
    )
