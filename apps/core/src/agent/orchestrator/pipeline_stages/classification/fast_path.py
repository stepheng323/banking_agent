"""Fast-path classification patterns for the orchestrator.

These patterns are used to classify simple, unambiguous intents
without needing to call the LLM.
"""

import re
from typing import Any

from apps.core.src.agent.orchestrator.models.classification import ClassificationResult

GREETING_PATTERNS = {
    "hi", "hello", "hey", "hey there", "hi there", "hello there",
    "good morning", "good afternoon", "good evening", "good night",
    "gm", "gn", "morning", "afternoon", "evening",
    "bawo", "kaaro", "ekaro", "sannu", "barka", "kedu", "ndewo",
    "how far", "how you dey",
}

CANCEL_PATTERNS = {"cancel", "stop", "abort", "nevermind", "forget it", "no thanks"}

CONFIRM_PATTERNS = {"yes", "ok", "sure", "confirm", "proceed"}

DECLINE_PATTERNS = {"no", "skip", "nope"}

TRANSFER_KEYWORDS = {"send", "transfer", "pay"}

QUERY_KEYWORDS = {"balance", "transaction", "history", "spent", "spending"}

MANAGE_ACCOUNT_KEYWORDS = {"account", "accounts", "link", "linked", "unlink", "default"}

MANAGE_ACCOUNT_PHRASES = {"show my", "list my", "my accounts", "linked account"}

QUERY_PATTERNS = {"balance", "history", "statement", "spent", "spending", "transaction"}


TRANSFER_PATTERN_1 = re.compile(r"^(send|transfer|pay)\s+(\d+(?:k|,\d+)?)\s+(?:to\s+)?(\w+)$")
TRANSFER_PATTERN_2 = re.compile(r"^pay\s+(\w+)\s+(\d+(?:k|,\d+)?)$")

AIRTIME_PATTERN = re.compile(r"^(?:buy\s+)?(?:airtime|recharge|topup|top up)\s+(\d+(?:k|,\d+)?)")

DATA_PATTERN_BUDGET = re.compile(r"^(?:buy\s+)?data\s+(\d+(?:k|,\d+)?)")
DATA_PATTERN_PHONE = re.compile(r"^(?:buy\s+)?data\s+(?:for\s+)?(0\d{10}|\+?234\d{10})")
DATA_PATTERN_SIMPLE = re.compile(r"^(?:buy\s+|i\s+(?:need|want)\s+)?data$")

ACCOUNT_NUMBER_PATTERN = re.compile(r"^\d{10}(\s*,?\s*\w+)?")
PHONE_NUMBER_PATTERN = re.compile(r"^0?\d{10,11}$")


def parse_amount(amount_raw: str) -> int:
    """Parse amount string like '5k' or '10,000' to integer."""
    if amount_raw.endswith("k"):
        return int(amount_raw[:-1]) * 1000
    return int(amount_raw.replace(",", ""))


def is_complex_request(text: str) -> bool:
    """Check if request is likely complex (multiple intents)."""
    text_lower = text.lower()
    has_transfer = any(kw in text_lower for kw in TRANSFER_KEYWORDS)
    has_query = any(kw in text_lower for kw in QUERY_KEYWORDS)
    has_conjunction = " and " in text_lower or " then " in text_lower

    if has_transfer and has_query:
        return True
    if has_transfer and has_conjunction and len(text_lower) > 30:
        return True
    return False


def match_transfer(text: str) -> ClassificationResult | None:
    """Try to match transfer patterns."""
    match1 = TRANSFER_PATTERN_1.match(text)
    if match1:
        return ClassificationResult(
            intent="transfer",
            is_complex=False,
            confidence=0.92,
            response="",
            complexity_reason="Simple transfer pattern",
        )

    match2 = TRANSFER_PATTERN_2.match(text)
    if match2:
        return ClassificationResult(
            intent="transfer",
            is_complex=False,
            confidence=0.92,
            response="",
            complexity_reason="Simple transfer pattern",
        )

    return None


def match_airtime(text: str) -> ClassificationResult | None:
    """Try to match airtime patterns."""
    if AIRTIME_PATTERN.match(text):
        return ClassificationResult(
            intent="airtime",
            is_complex=False,
            confidence=0.92,
            response="",
            complexity_reason="Simple airtime pattern",
        )
    return None


def match_data(text: str) -> ClassificationResult | None:
    """Try to match data patterns."""
    if DATA_PATTERN_BUDGET.match(text):
        match = DATA_PATTERN_BUDGET.match(text)
        amount = parse_amount(match.group(1))
        return ClassificationResult(
            intent="data",
            is_complex=False,
            confidence=0.92,
            response=f"Looking for data plans within ₦{amount:,}...",
            complexity_reason="Simple data pattern with budget",
        )

    if DATA_PATTERN_PHONE.match(text):
        match = DATA_PATTERN_PHONE.match(text)
        phone = match.group(1)
        return ClassificationResult(
            intent="data",
            is_complex=False,
            confidence=0.92,
            response=f"I'll help you buy data for {phone[:4]}•••{phone[-4:]}...",
            complexity_reason="Simple data pattern with phone",
        )

    if DATA_PATTERN_SIMPLE.match(text):
        return ClassificationResult(
            intent="data",
            is_complex=False,
            confidence=0.92,
            response="I'll help you get a data plan...",
            complexity_reason="Simple data request",
        )

    return None
