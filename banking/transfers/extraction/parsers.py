"""Deterministic parsers used by transfer extraction."""

import re
from typing import Any

from banking.presentation.formatters.currency import format_naira
from banking.transactions.shared.source_account_guard import find_account_by_bank_name
from banking.transfers.models.types import TransferContext, TransferPayload
from shared.utils.bank_aliases import display_bank_name, is_known_bank_alias
from shared.utils.sanitize import normalize_bank_account_number

_ACCOUNT_BANK_ACCOUNT_FIRST_PATTERN = re.compile(r"^\s*(?P<account>(?:\d[\s,.\-]?){10,11})\s+(?P<bank>.+?)\s*$")
_ACCOUNT_BANK_BANK_FIRST_PATTERN = re.compile(r"^\s*(?P<bank>.+?)\s+(?P<account>(?:\d[\s,.\-]?){10,11})\s*$")
_ACCOUNT_LABEL_PATTERN = re.compile(
    r"\b(?:account\s*(?:number|no\.?|#)?|acct(?:\s*(?:number|no\.?))?|a/c)\b"
    r"\s*[:\-]?\s*(?P<account>(?:\d[\s,.\-]?){10,11})(?!\d)",
    re.IGNORECASE,
)
_BANK_LABEL_PATTERN = re.compile(
    r"(?:^|\n)\s*bank(?:\s+name)?\s*[:\-]\s*(?P<bank>[^\n;]+)",
    re.IGNORECASE,
)
_BANK_DETAIL_INLINE_NON_BANK_RE = re.compile(r"\b(?:send|transfer|pay|remit)\b", re.IGNORECASE)
_BANK_ONLY_SLOT_REPLY_BLOCK_RE = re.compile(
    r"\b(send|transfer|pay|buy|airtime|data|bundle|balance|statement|transaction|transactions|"
    r"account\s+balance|support|faq|cancel|stop|show|list|check)\b",
    re.IGNORECASE,
)
_BANK_ONLY_SLOT_REPLY_RE = re.compile(
    r"^(?:(?:it'?s|its|it is|bank is|the bank is|use)\s+)?(?P<bank>[a-z0-9&' .-]+?)(?:\s+bank)?$",
    re.IGNORECASE,
)
_RECIPIENT_SLOT_REPLY_PREFIX_RE = re.compile(
    r"^(?:(?:it'?s|its|it is|this is)\s+)?(?:(?:to|for|send(?:\s+it)?\s+to)\s+)?(?P<recipient>.+?)$",
    re.IGNORECASE,
)
_RECIPIENT_SLOT_REPLY_BLOCK_RE = re.compile(
    r"\b(and|also|plus|then|while|cancel|stop|show|list|check|buy|help|support|faq|balance|"
    r"statement|spend|spent|transaction|transactions|airtime|data|beneficiar(?:y|ies)|"
    r"account(?:s)?|week|month|today|tomorrow|yesterday)\b",
    re.IGNORECASE,
)
_RECIPIENT_SLOT_REPLY_QUESTION_RE = re.compile(r"^(what|how|why|when|where|who|which)\b", re.IGNORECASE)
_RECIPIENT_SLOT_REPLY_META_RE = re.compile(
    r"^(hi|hello|hey|thanks|thank you|ok|okay|sure|yes|no)$",
    re.IGNORECASE,
)
_AMOUNT_REPLY_PATTERN = re.compile(
    r"^\s*(?:₦|ngn)?\s*(?P<amount>\d[\d,]*(?:\.\d+)?)\s*(?P<suffix>[kKhH]?)\s*$",
    re.IGNORECASE,
)
_AMOUNT_COMMAND_REPLY_PATTERN = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|please|pls|abeg|oya|jowo|biko|kindly)\s+)*"
    r"(?:send|transfer|pay|remit)\s+"
    r"(?:₦|ngn)?\s*(?P<amount>\d[\d,]*(?:\.\d+)?)\s*(?P<suffix>[kKhH]?)\s*[.!?]?\s*$",
    re.IGNORECASE,
)
_SIMPLE_TRANSFER_PREFIX_RE = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|please|pls|abeg|oya|jowo|biko|kindly)\s+)*"
    r"(?P<verb>send|transfer|pay|remit)\s+"
    r"(?P<amount>(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKhH]?)\s+"
    r"(?:to|for)\s+"
    r"(?P<target>.+?)\s*$",
    re.IGNORECASE,
)
_SIMPLE_TRANSFER_COMPLEX_MARKERS_RE = re.compile(
    r"\b(?:each|split|between|btw|half|quarter|tithe|all|everything|from|using|use|with|"
    r"tomorrow|tommorow|next week|weekly|monthly|every|abroad|international)\b",
    re.IGNORECASE,
)
_RECIPIENT_SCHEDULE_SUFFIX_RE = re.compile(
    r"\s+(?:by\s+)?(?:tomorrow|tommorow|today|later|next\s+\w+|on\s+\d{4}-\d{2}-\d{2})\b.*$",
    re.IGNORECASE,
)
_SIMPLE_TRANSFER_MULTI_TARGET_RE = re.compile(r"\s(?:and|&)\s|,", re.IGNORECASE)
_CONFIRMATION_EDIT_PREFIX_RE = re.compile(
    r"^(?:(?:make|change|update)\s+(?:it|amount)\s*(?:to\s*)?|"
    r"(?:make|change|update)\s+to\s+|"
    r"send\s+)?",
    re.IGNORECASE,
)
_CONFIRMATION_PERCENTAGE_RE = re.compile(r"^(?P<pct>\d{1,3}(?:\.\d+)?)\s*%$", re.IGNORECASE)
_CONFIRMATION_BANK_SWITCH_RE = re.compile(
    r"^(?:(?:use|switch(?:\s+to)?|change(?:\s+to)?)\s+)?[a-z0-9&' ]+(?:\s+bank)?(?:\s+instead)?$",
    re.IGNORECASE,
)
_CONFIRMATION_NARRATION_RE = re.compile(
    r"^(?:"
    r"(?:add\s+that\s+)?(?:it'?s|its|it\s+is|this\s+is)\s+for\s+(?P<for_text>.+)"
    r"|for\s+(?P<bare_for_text>.+)"
    r"|(?:the\s+)?(?:narration|memo|note|description|reason|purpose)(?:\s+(?:should\s+be|is|as|to\s+be|to))?[:\s]+(?P<label_text>.+)"
    r"|use\s+(?P<use_text>.+?)\s+as\s+(?:narration|memo|note|description)"
    r")$",
    re.IGNORECASE,
)
_MEDIA_CAPTION_NARRATION_RE = re.compile(
    r"^\s*Caption-derived transfer fields:\s*narration=(?P<narration>.+?)\.?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_MEDIA_CAPTION_AMOUNT_RE = re.compile(
    r"^\s*Caption-derived transfer fields:\s*amount=(?P<amount>\d[\d,]*(?:\.\d+)?)\.?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def parse_bank_name_slot_reply(user_message: str | None) -> str | None:
    text = (user_message or "").strip()
    if not text or "?" in text:
        return None
    if _BANK_ONLY_SLOT_REPLY_BLOCK_RE.search(text):
        return None
    match = _BANK_ONLY_SLOT_REPLY_RE.fullmatch(text)
    if match is None:
        return None
    bank = (match.group("bank") or "").strip(" \t\r\n.,;:\"'()[]{}")
    if not bank or bank.isdigit():
        return None
    tokens = bank.split()
    if not tokens or len(tokens) > 4 or any(len(token) < 2 for token in tokens):
        return None
    return bank


def parse_recipient_name_slot_reply(user_message: str | None) -> str | None:
    text = (user_message or "").strip()
    if not text or "?" in text:
        return None
    if _RECIPIENT_SLOT_REPLY_META_RE.fullmatch(text):
        return None
    if _RECIPIENT_SLOT_REPLY_QUESTION_RE.search(text) or _RECIPIENT_SLOT_REPLY_BLOCK_RE.search(text):
        return None
    if parse_account_and_bank_input(text):
        return None
    match = _RECIPIENT_SLOT_REPLY_PREFIX_RE.fullmatch(text)
    if match is None:
        return None
    recipient = (match.group("recipient") or "").strip(" \t\r\n.,;:\"'()[]{}")
    if not recipient or recipient.isdigit():
        return None
    tokens = recipient.split()
    if not tokens or len(tokens) > 4 or any(len(token) < 2 for token in tokens):
        return None
    if len(tokens) <= 2 and recipient.casefold().endswith(" bank"):
        return None
    return recipient


def extract_media_caption_narration(user_message: str) -> str | None:
    match = _MEDIA_CAPTION_NARRATION_RE.search(user_message or "")
    if not match:
        return None
    narration = re.sub(r"\s+", " ", match.group("narration")).strip(" \t\r\n\"'`.,;:")
    if not narration or len(narration) > 80:
        return None
    return narration


def extract_media_caption_amount(user_message: str) -> float | None:
    match = _MEDIA_CAPTION_AMOUNT_RE.search(user_message or "")
    if not match:
        return None
    try:
        amount = float(match.group("amount").replace(",", ""))
    except ValueError:
        return None
    return amount if amount > 0 else None


def strip_recipient_schedule_suffix(value: str | None) -> str | None:
    if not value:
        return value
    stripped = _RECIPIENT_SCHEDULE_SUFFIX_RE.sub("", value).strip(" \t\r\n,.;:!?")
    stripped = re.sub(r"\s+", " ", stripped).strip()
    return stripped or None


def recipient_schedule_cleanup_patch(data: TransferPayload) -> dict[str, Any]:
    cleaned = strip_recipient_schedule_suffix(data.recipient_name)
    if cleaned and cleaned != data.recipient_name:
        return {"recipient_name": cleaned}
    return {}


def should_override_skip_extraction(payload: TransferPayload) -> bool:
    """Return True when planner-provided recipient text still needs LLM entity extraction."""
    if payload.recipient_account and (payload.recipient_bank_name or payload.recipient_bank_code):
        return False

    recipient_hint = (payload.recipient_name or "").strip()
    if not recipient_hint:
        return False

    digits_only = "".join(ch for ch in recipient_hint if ch.isdigit())
    return len(digits_only) >= 10


def parse_account_and_bank_input(user_message: str) -> tuple[str, str] | None:
    """Parse account+bank in either order, normalizing account separators."""
    text = user_message.strip()
    if not text:
        return None

    account_match = _ACCOUNT_LABEL_PATTERN.search(text)
    bank_match = _BANK_LABEL_PATTERN.search(text)
    if account_match and bank_match:
        normalized_account = normalize_bank_account_number(account_match.group("account"))
        bank_name = bank_match.group("bank").strip().strip("*_` ,.-")
        if (
            len(normalized_account) == 10
            and bank_name
            and not bank_name.isdigit()
            and not _BANK_DETAIL_INLINE_NON_BANK_RE.search(bank_name)
            and is_known_bank_alias(bank_name)
        ):
            known_bank = display_bank_name(bank_name)
            if known_bank:
                return normalized_account, known_bank

    for pattern in (_ACCOUNT_BANK_ACCOUNT_FIRST_PATTERN, _ACCOUNT_BANK_BANK_FIRST_PATTERN):
        match = pattern.match(re.sub(r"\s+", " ", text.replace("\n", " ")).strip())
        if not match:
            continue

        normalized_account = normalize_bank_account_number(match.group("account"))
        bank_name = match.group("bank").strip().strip(",.- ")

        if len(normalized_account) != 10:
            continue
        if not bank_name or bank_name.isdigit():
            continue
        if _BANK_DETAIL_INLINE_NON_BANK_RE.search(bank_name):
            continue
        if not is_known_bank_alias(bank_name):
            continue

        known_bank = display_bank_name(bank_name)
        if known_bank:
            return normalized_account, known_bank

    return None


def parse_amount_input(user_message: str) -> float | None:
    """Parse shorthand amount replies like '20k', '20000', '₦20,000'."""
    text = user_message.strip()
    match = _AMOUNT_REPLY_PATTERN.match(text) or _AMOUNT_COMMAND_REPLY_PATTERN.match(text)
    if not match:
        return None

    try:
        value = float(match.group("amount").replace(",", ""))
    except ValueError:
        return None

    suffix = match.group("suffix").lower()
    multiplier = 1000.0 if suffix == "k" else 100.0 if suffix == "h" else 1.0
    amount = value * multiplier
    if amount <= 0:
        return None
    return amount


def parse_simple_transfer_command(
    user_message: str,
    current_payload: TransferPayload,
    beneficiaries: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Deterministically parse obvious single-recipient send commands."""
    if any(
        (
            current_payload.amount is not None,
            current_payload.transfer_percentage is not None,
            current_payload.transfer_all,
            current_payload.recipient_name,
            current_payload.recipient_account,
            current_payload.recipient_bank_name,
            current_payload.source_bank_name,
            current_payload.source_accounts,
            current_payload.use_dual_accounts is not None,
            current_payload.explicit_split,
        )
    ):
        return None

    normalized = re.sub(r"\s+", " ", user_message.strip())
    if not normalized:
        return None
    if _SIMPLE_TRANSFER_COMPLEX_MARKERS_RE.search(normalized):
        return None

    match = _SIMPLE_TRANSFER_PREFIX_RE.match(normalized)
    if not match:
        return None

    target = strip_recipient_schedule_suffix(match.group("target").strip().strip(".!?")) or ""
    if not target or _SIMPLE_TRANSFER_MULTI_TARGET_RE.search(target):
        return None

    amount = parse_amount_input(match.group("amount"))
    if amount is None or amount < 1000:
        return None

    patch: dict[str, Any] = {
        "amount": float(amount),
        "confirmation": {"confirmed": False},
        "suggested_amount": None,
        "transfer_percentage": None,
        "transfer_all": False,
    }

    account_and_bank = parse_account_and_bank_input(target)
    if account_and_bank is not None:
        recipient_account, recipient_bank_name = account_and_bank
        patch.update(
            {
                "recipient_account": recipient_account,
                "recipient_bank_name": recipient_bank_name,
                "recipient_bank_code": None,
                "recipient_bank_code_provider": None,
                "recipient_resolution_provider": None,
                "recipient_resolution_mode": None,
                "recipient_resolved_name": None,
                "name_mismatch": False,
                "name_match_score": None,
                "name_mismatch_warning": None,
            }
        )
        return patch

    normalized_account = normalize_bank_account_number(target)
    if len(normalized_account) == 10 and not any(c.isalpha() for c in target):
        patch["recipient_account"] = normalized_account
        return patch

    if target.isdigit():
        return None

    if any(c.isdigit() for c in target):
        return None

    # A saved alias can legitimately contain a bank word (for example
    # "Tolu Access").  Bind only an exact, unique alias before the generic
    # bank-token guard below decides the phrase needs LLM interpretation.
    normalized_target = normalize_name_token(target)
    exact_alias_matches = [
        beneficiary
        for beneficiary in beneficiaries or []
        if isinstance(beneficiary, dict)
        and normalize_name_token(str(beneficiary.get("alias") or "")) == normalized_target
    ]
    if len(exact_alias_matches) == 1:
        beneficiary = exact_alias_matches[0]
        alias = str(beneficiary.get("alias") or "").strip()
        beneficiary_id = str(beneficiary.get("id") or "").strip()
        if alias and beneficiary_id:
            account_number = str(beneficiary.get("account_number") or "").strip()
            bank_name = str(beneficiary.get("bank_name") or "").strip()
            bank_code = str(beneficiary.get("bank_code") or "").strip()
            patch.update(
                {
                    "recipient_name": alias,
                    "beneficiary_id": beneficiary_id,
                    # Compact context intentionally carries aliases without
                    # destination fields.  Mark it for safe executor-side
                    # hydration rather than asking for an account number.
                    "beneficiary_candidates": (
                        []
                        if account_number and (bank_name or bank_code)
                        else [{"beneficiary_id": beneficiary_id, "recipient_name": alias}]
                    ),
                }
            )
            if account_number:
                patch["recipient_account"] = account_number
            if bank_name:
                patch["recipient_bank_name"] = bank_name
            if bank_code:
                patch["recipient_bank_code"] = bank_code
            return patch

    from shared.utils.bank_aliases import BANK_ALIASES, BANK_DISPLAY_NAMES
    target_lower = f" {target.lower()} "
    for bank_key in list(BANK_ALIASES.keys()) + list(BANK_DISPLAY_NAMES.keys()):
        if f" {bank_key} " in target_lower:
            return None

    patch["recipient_name"] = target
    return patch


def normalize_name_token(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def recipient_name_matches_existing_binding(new_name: str | None, current_payload: TransferPayload) -> bool:
    normalized_new = normalize_name_token(new_name)
    if not normalized_new:
        return False

    normalized_known = {
        normalize_name_token(current_payload.recipient_name),
        normalize_name_token(current_payload.recipient_resolved_name),
    }
    normalized_known.discard("")
    if not normalized_known:
        return False

    if normalized_new in normalized_known:
        return True

    new_tokens = set(normalized_new.split())
    if not new_tokens:
        return False

    for known in normalized_known:
        known_tokens = set(known.split())
        if not known_tokens:
            continue
        if known_tokens.issubset(new_tokens) or new_tokens.issubset(known_tokens):
            return True

    return False


def _normalize_user_message(value: str) -> str:
    compact = re.sub(r"\s+", " ", value.strip().lower())
    return compact.strip('.,!?;:"`~()[]{}')


def _format_amount_ack(amount: float) -> str:
    rounded = float(amount)
    if rounded.is_integer():
        integer_amount = int(rounded)
        if integer_amount >= 1000 and integer_amount % 1000 == 0:
            return f"Changing amount to {integer_amount // 1000}k."
        return f"Changing amount to {format_naira(integer_amount)}."
    return f"Changing amount to {format_naira(rounded, decimal_places=2)}."


def _normalize_note_text(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.strip())
    return normalized.strip(" .,!?:;\"'`~()[]{}")


def _looks_like_explicit_bank_switch(value: str) -> bool:
    normalized = _normalize_user_message(value)
    if not normalized:
        return False
    return bool(
        re.search(r"\b(?:use|switch|change)\b", normalized)
        or normalized.endswith(" bank")
        or normalized.endswith(" instead")
    )


def _parse_single_confirmation_amount_edit(user_message: str) -> dict[str, Any] | None:
    normalized = _normalize_user_message(user_message)
    if not normalized:
        return None

    candidate = _CONFIRMATION_EDIT_PREFIX_RE.sub("", normalized, count=1).strip() or normalized
    patch: dict[str, Any] | None = None

    parsed_amount = parse_amount_input(candidate)
    if parsed_amount is not None:
        patch = {
            "amount": parsed_amount,
            "transfer_percentage": None,
            "transfer_all": False,
            "funding_plan": None,
            "suggested_amount": None,
            "confirmation": {"confirmed": False},
            "transition_acknowledgment": _format_amount_ack(parsed_amount),
        }
    elif candidate in {"all", "everything"}:
        patch = {
            "amount": None,
            "transfer_percentage": None,
            "transfer_all": True,
            "funding_plan": None,
            "suggested_amount": None,
            "confirmation": {"confirmed": False},
            "transition_acknowledgment": "Sending all available funds.",
        }
    elif candidate == "half":
        patch = {
            "amount": None,
            "transfer_percentage": 50.0,
            "transfer_all": False,
            "funding_plan": None,
            "suggested_amount": None,
            "confirmation": {"confirmed": False},
            "transition_acknowledgment": "Changing transfer to half of the available balance.",
        }
    else:
        pct_match = _CONFIRMATION_PERCENTAGE_RE.fullmatch(candidate)
        if pct_match:
            pct_value = float(pct_match.group("pct"))
            if 0 < pct_value <= 100:
                patch = {
                    "amount": None,
                    "transfer_percentage": pct_value,
                    "transfer_all": False,
                    "funding_plan": None,
                    "suggested_amount": None,
                    "confirmation": {"confirmed": False},
                    "transition_acknowledgment": f"Changing transfer to {pct_value:g}% of the available balance.",
                }

    if patch is None:
        return None
    return patch


def _parse_single_confirmation_source_bank_edit(
    user_message: str,
    current_payload: TransferPayload,
    accounts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    normalized = _normalize_user_message(user_message)
    if not normalized or not _CONFIRMATION_BANK_SWITCH_RE.fullmatch(normalized):
        return None

    candidate = re.sub(r"^(?:use|switch(?:\s+to)?|change(?:\s+to)?)\s+", "", normalized, flags=re.IGNORECASE)
    candidate = re.sub(r"\s+instead$", "", candidate, flags=re.IGNORECASE).strip()
    if not candidate:
        return None

    matched_account = find_account_by_bank_name(accounts, candidate)
    if not matched_account:
        return None

    bank_name = str(matched_account.get("bank_name") or "").strip()
    if not bank_name:
        return None
    if current_payload.source_bank_name and bank_name.lower() == current_payload.source_bank_name.lower():
        return None

    patch: dict[str, Any] = {
        "source_bank_name": bank_name,
        "source_account_id": None,
        "source_account_name": None,
        "source_account_number": None,
        "source_account_index": None,
        "source_affinity_mode": "explicit",
        "funding_plan": None,
        "suggested_amount": None,
        "confirmation": {"confirmed": False},
        "transition_acknowledgment": f"Using {bank_name} instead.",
    }
    return patch


def _parse_single_confirmation_narration_edit(user_message: str) -> dict[str, Any] | None:
    normalized = _normalize_user_message(user_message)
    if not normalized:
        return None

    match = _CONFIRMATION_NARRATION_RE.fullmatch(normalized)
    if not match:
        return None

    raw_note = next((group for group in match.groups() if group), "")
    note = _normalize_note_text(raw_note)
    if not note:
        return None
    if note.isdigit():
        return None
    if note in {"all", "everything", "half"}:
        return None
    if parse_amount_input(note) is not None:
        return None
    if _CONFIRMATION_PERCENTAGE_RE.fullmatch(note):
        return None
    if _looks_like_explicit_bank_switch(note):
        return None
    if parse_account_and_bank_input(note) is not None:
        return None

    patch: dict[str, Any] = {
        "authored_narration": note,
        "narration": note,
        "user_note": note,
        "confirmation": {"confirmed": False},
        "transition_acknowledgment": "Added narration.",
    }
    return patch


def parse_confirmation_narration_edit(user_message: str) -> dict[str, Any] | None:
    """Parse a single-transfer confirmation narration update without requiring prior snapshot state."""
    return _parse_single_confirmation_narration_edit(user_message)


def parse_single_confirmation_transfer_edit(
    user_message: str,
    current_payload: TransferPayload,
    context: TransferContext,
    worker_context: Any,
) -> dict[str, Any] | None:
    confirmation_task_count = getattr(worker_context, "confirmation_task_count", None)
    scoped_confirmation_message = bool(current_payload.confirmation_message_scoped)
    if confirmation_task_count != 1 and not scoped_confirmation_message:
        return None
    if not current_payload.previous_confirmation_snapshot:
        return None

    amount_patch = _parse_single_confirmation_amount_edit(user_message)
    if amount_patch is not None:
        return amount_patch

    accounts = context.accounts if isinstance(context.accounts, list) else []
    source_bank_patch = _parse_single_confirmation_source_bank_edit(user_message, current_payload, accounts)
    if source_bank_patch is not None:
        return source_bank_patch

    return _parse_single_confirmation_narration_edit(user_message)
