"""Transfer-specific repairs for planner task normalization."""

import re

from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_normalizer_parsing import (
    BALANCE_SHARE_PERCENT_PATTERN,
    extract_account_candidates,
    extract_amount_candidates,
    extract_bank_candidates,
    parse_amount_value,
    single_unambiguous,
)
from shared.money import MoneyAmount
from shared.types.planner import TransferTaskParameters

_BATCH_RECIPIENT_HINT_RE = re.compile(r"\b(?:each|split|between)\b", re.IGNORECASE)
_RECIPIENT_JOIN_RE = re.compile(r"\b(?:and|plus)\b|,", re.IGNORECASE)
_SOURCE_FIRST_TRANSFER_RE = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|please|pls|abeg|oya|jowo|biko|kindly)\s+)*"
    r"(?:use|using|from|with)\s+(?:my\s+)?(?P<source>.+?)\s+"
    r"(?:to\s+)?(?:send|transfer|pay|remit)\b(?P<tail>.*)$",
    re.IGNORECASE,
)


def _balance_share_percent(text: str) -> float | None:
    pct_match = BALANCE_SHARE_PERCENT_PATTERN.search(text)
    if pct_match is not None:
        raw_pct = float(pct_match.group("pct"))
        if 0 < raw_pct <= 100:
            return raw_pct
    if re.search(r"\bhalf\b", text):
        return 50.0
    if re.search(r"\bquarter\b", text):
        return 25.0
    if re.search(r"\btithe\b", text):
        return 10.0
    return None


def normalize_transfer_amount_field(params: TransferTaskParameters) -> list[str]:
    patched: list[str] = []
    raw_amount = params.amount
    if not isinstance(raw_amount, str):
        parsed_amount = parse_amount_value(raw_amount)
        if parsed_amount is not None and parsed_amount <= 0:
            params.amount = None
            patched.append("amount")
        return patched

    lowered = raw_amount.strip().lower()
    if not lowered:
        params.amount = None
        patched.append("amount")
        return patched

    parsed_amount = parse_amount_value(raw_amount)
    if parsed_amount is not None:
        params.amount = parsed_amount if parsed_amount > 0 else None
        patched.append("amount")
        return patched

    pct_value = _balance_share_percent(lowered)
    if pct_value is not None:
        params.amount = None
        params.transfer_percentage = pct_value
        params.transfer_all = False
        patched.extend(["amount", "transfer_percentage"])
        return patched

    if re.search(r"\b(?:all|everything|max amount|whatever i have)\b", lowered):
        params.amount = None
        params.transfer_all = True
        params.transfer_percentage = None
        patched.extend(["amount", "transfer_all"])

    return patched


def _looks_like_recipient_batch_text(text: str, params: TransferTaskParameters) -> bool:
    if params.recipient_allocations and len(params.recipient_allocations) >= 2:
        return True
    if not _BATCH_RECIPIENT_HINT_RE.search(text):
        return False
    return bool(_RECIPIENT_JOIN_RE.search(text))


def _source_first_transfer_parts(text: str) -> tuple[str, str] | None:
    match = _SOURCE_FIRST_TRANSFER_RE.match(text)
    if match is None:
        return None
    return match.group("source"), match.group("tail")


def _source_first_bank_candidate(text: str) -> str | None:
    parts = _source_first_transfer_parts(text)
    if parts is None:
        return None
    source_bank, source_bank_ambiguous = single_unambiguous(extract_bank_candidates(parts[0]))
    if source_bank_ambiguous or not isinstance(source_bank, str):
        return None
    return source_bank


def repair_source_first_transfer_params(params: TransferTaskParameters, text: str) -> list[str]:
    patched: list[str] = []
    source_bank = _source_first_bank_candidate(text)
    if not source_bank:
        return patched

    if not params.source_bank_name:
        params.source_bank_name = source_bank
        patched.append("source_bank_name")

    has_destination_account = bool(params.recipient_account or extract_account_candidates(text))
    if params.bank_name and not has_destination_account:
        params.bank_name = None
        patched.append("bank_name")

    return patched


def normalize_transfer_params(
    params: TransferTaskParameters,
    text: str,
) -> tuple[list[str], list[str]]:
    patched: list[str] = []
    ambiguous: list[str] = []

    if params.is_self is True:
        # The planner signal is authoritative.  Do not let a sibling's
        # recipient alias survive normalization on a linked-account leg.
        if params.recipient or params.recipient_name:
            params.recipient = None
            params.recipient_name = None
            patched.extend(["recipient", "recipient_name"])

    account_ambiguous = False
    if not params.recipient_account:
        account, account_ambiguous = single_unambiguous(extract_account_candidates(text))
        if account_ambiguous:
            ambiguous.append("recipient_account")
        elif isinstance(account, str):
            params.recipient_account = account
            patched.append("recipient_account")

    # Keep account+bank pairing strict for transfer destination.
    source_first_parts = _source_first_transfer_parts(text)
    should_infer_bank = bool(params.recipient_account) and not _looks_like_recipient_batch_text(text, params)
    if source_first_parts is not None and not params.recipient_account:
        should_infer_bank = False
    if not params.bank_name and not account_ambiguous and should_infer_bank:
        bank_text = source_first_parts[1] if source_first_parts is not None else text
        bank_name, bank_ambiguous = single_unambiguous(extract_bank_candidates(bank_text))
        if bank_ambiguous:
            ambiguous.append("bank_name")
        elif isinstance(bank_name, str):
            params.bank_name = bank_name
            patched.append("bank_name")

    if params.amount is None:
        amount, amount_ambiguous = single_unambiguous(extract_amount_candidates(text))
        if amount_ambiguous:
            ambiguous.append("amount")
        elif amount is not None:
            params.amount = amount
            patched.append("amount")

    return patched, ambiguous


def repair_account_aware_transfer_params(params: TransferTaskParameters, text: str) -> list[str]:
    patched: list[str] = []
    lowered = (text or "").lower()

    if params.transfer_percentage is None and not params.transfer_all and params.amount is None:
        pct_value = _balance_share_percent(lowered)
        if pct_value is not None:
            params.transfer_percentage = pct_value
            params.amount = None
            params.transfer_all = False
            patched.append("transfer_percentage")

    if (
        not params.transfer_all
        and params.transfer_percentage is None
        and params.amount is None
        and re.search(r"\b(?:all|everything|max amount|whatever i have)\b", lowered)
    ):
        params.transfer_all = True
        params.amount = None
        patched.append("transfer_all")

    return patched


def sanitize_transfer_explicit_split(params: TransferTaskParameters) -> list[str]:
    explicit_split = params.explicit_split
    if not isinstance(explicit_split, dict):
        return []

    allocations = params.recipient_allocations or []
    if len(allocations) < 2:
        return []

    cleaned_split: dict[str, MoneyAmount] = {}
    for raw_key, raw_value in explicit_split.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        if re.search(r"[\[\]\{\}:\"']", key):
            continue
        normalized_key = re.sub(r"[^a-z0-9]+", " ", key.lower()).strip()
        if not normalized_key:
            continue
        if normalized_key in {"amount", "recipient", "recipient name", "recipient allocations", "allocations"}:
            continue
        if "recipient" in normalized_key.split():
            continue
        amount = parse_amount_value(raw_value)
        if amount is None or amount <= 0:
            continue
        cleaned_split[key] = amount

    if cleaned_split == explicit_split:
        return []

    params.explicit_split = cleaned_split or None
    return ["explicit_split"]
