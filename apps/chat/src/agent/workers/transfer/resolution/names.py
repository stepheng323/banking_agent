"""Recipient-name and beneficiary-binding helpers for transfer resolution."""

import difflib
import re
import unicodedata
from typing import Any

from apps.chat.src.agent.workers.transfer.models.types import TransferPayload
from banking.policy.guardrails.loader import get_cached_guardrails
from banking.presentation.formatters.recipient_prompt_names import sanitize_recipient_display_name
from banking.presentation.i18n.renderer import render_message
from shared.config.settings import settings

_RECIPIENT_PRONOUN_TOKENS = {"her", "him", "them", "that", "it", "this", "previous"}
_UNSAFE_RECIPIENT_TOKENS = _RECIPIENT_PRONOUN_TOKENS | {"send", "transfer", "pay", "recipient", "s"}


def provider_name(provider: Any, default: str | None = None) -> str:
    value = getattr(provider, "provider_name", None)
    return str(value or default or settings.transfer_resolver_provider_name).strip().lower()


def optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "none":
        return None
    return text


def beneficiary_provider(value: Any, bank_code: str | None) -> str | None:
    if not bank_code:
        return None
    provider = optional_text(value)
    return (provider or settings.beneficiary_resolver_provider_name).lower()


def canonical_beneficiary_id(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if text.startswith("bene:"):
        return text.split(":", 1)[1].strip() or None
    return text


def normalize_name(value: str | None) -> str:
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", without_marks.lower()).strip()


def recipient_tokens(value: str | None) -> list[str]:
    if not value:
        return []
    lowered = value.strip().lower()
    lowered = re.sub(r"([a-z])['’]s\b", r"\1", lowered)
    normalized = re.sub(r"[^a-z0-9]+", " ", lowered).strip()
    return [token for token in normalized.split() if token]


def is_pronoun_recipient(value: str | None) -> bool:
    tokens = recipient_tokens(value)
    return bool(tokens) and all(token in _RECIPIENT_PRONOUN_TOKENS for token in tokens)


def is_unsafe_recipient_placeholder(value: str | None) -> bool:
    tokens = recipient_tokens(value)
    return bool(tokens) and all(token in _UNSAFE_RECIPIENT_TOKENS for token in tokens)


def similarity_score(left: str | None, right: str | None) -> float:
    norm_left = normalize_name(left)
    norm_right = normalize_name(right)
    if not norm_left or not norm_right:
        return 1.0

    base_ratio = difflib.SequenceMatcher(a=norm_left, b=norm_right).ratio()
    sorted_left = " ".join(sorted(norm_left.split()))
    sorted_right = " ".join(sorted(norm_right.split()))
    token_ratio = difflib.SequenceMatcher(a=sorted_left, b=sorted_right).ratio()
    return max(base_ratio, token_ratio)


def is_relational_alias(name: str | None, aliases: list[str]) -> bool:
    normalized = normalize_name(name)
    if not normalized:
        return False
    normalized_aliases = {normalize_name(alias) for alias in aliases}
    return normalized in normalized_aliases


def build_name_consistency_patch(payload: TransferPayload, resolved_name: str | None, locale: str) -> dict[str, Any]:
    if not resolved_name:
        return {"name_mismatch": False, "name_match_score": None, "name_mismatch_warning": None}

    requested_name = (payload.recipient_name or "").strip()
    if not requested_name:
        return {"name_mismatch": False, "name_match_score": None, "name_mismatch_warning": None}
    if is_unsafe_recipient_placeholder(requested_name):
        return {"name_mismatch": False, "name_match_score": None, "name_mismatch_warning": None}

    digits_only = "".join(ch for ch in requested_name if ch.isdigit())
    if len(digits_only) >= 8:
        return {"name_mismatch": False, "name_match_score": None, "name_mismatch_warning": None}

    guardrails = get_cached_guardrails()
    aliases = guardrails.transfer.relational_aliases
    if is_relational_alias(requested_name, aliases):
        return {"name_mismatch": False, "name_match_score": None, "name_mismatch_warning": None}

    score = similarity_score(requested_name, resolved_name)
    threshold = float(guardrails.transfer.name_match.min_similarity)
    if score < threshold:
        warning = render_message(
            "transfer.confirmation.name_mismatch_warning",
            locale,
            {"requested_name": requested_name, "resolved_name": resolved_name},
        )
        return {
            "name_mismatch": True,
            "name_match_score": round(score, 2),
            "name_mismatch_warning": warning,
        }

    return {
        "name_mismatch": False,
        "name_match_score": round(score, 2),
        "name_mismatch_warning": None,
    }


def matches_selected_beneficiary(payload: TransferPayload, selected: dict[str, Any]) -> bool:
    """Return True when current payload still targets the selected beneficiary."""
    selected_account = str(selected.get("account_number") or "").strip()
    selected_bank_code = str(selected.get("bank_code") or "").strip()
    selected_bank_code_provider = (
        str(selected.get("bank_code_provider") or settings.beneficiary_resolver_provider_name).strip().lower()
    )
    selected_bank_name = str(selected.get("bank_name") or "").strip()
    selected_alias = str(selected.get("alias") or "").strip()
    selected_account_name = str(selected.get("account_name") or "").strip()

    req_account = str(payload.recipient_account or "").strip()
    if req_account and selected_account and req_account != selected_account:
        return False

    req_bank_code = str(payload.recipient_bank_code or "").strip()
    req_bank_code_provider = str(payload.recipient_bank_code_provider or selected_bank_code_provider).strip().lower()
    if (
        req_bank_code
        and selected_bank_code
        and req_bank_code_provider == selected_bank_code_provider
        and req_bank_code != selected_bank_code
    ):
        return False

    req_bank_name = str(payload.recipient_bank_name or "").strip()
    if req_bank_name and selected_bank_name and normalize_name(req_bank_name) != normalize_name(selected_bank_name):
        return False

    req_name = normalize_name(payload.recipient_name)
    if req_name:
        candidates = [normalize_name(selected_alias), normalize_name(selected_account_name)]
        if not any(req_name and cand and (req_name in cand or cand in req_name) for cand in candidates):
            return False

    return True


def clear_stale_beneficiary_binding(payload: TransferPayload, selected: dict[str, Any]) -> None:
    """Detach payload from previously selected beneficiary when recipient changes."""
    selected_account = str(selected.get("account_number") or "").strip()
    selected_bank_code = str(selected.get("bank_code") or "").strip()
    selected_bank_code_provider = (
        str(selected.get("bank_code_provider") or settings.beneficiary_resolver_provider_name).strip().lower()
    )
    selected_bank_name = str(selected.get("bank_name") or "").strip()

    payload.beneficiary_id = None
    payload.recipient_resolved_name = None
    payload.resolved_from_saved_beneficiary = False

    req_account = str(payload.recipient_account or "").strip()
    if req_account and selected_account and req_account == selected_account:
        payload.recipient_account = None

    req_bank_code = str(payload.recipient_bank_code or "").strip()
    req_bank_code_provider = str(payload.recipient_bank_code_provider or selected_bank_code_provider).strip().lower()
    if (
        req_bank_code
        and selected_bank_code
        and req_bank_code_provider == selected_bank_code_provider
        and req_bank_code == selected_bank_code
    ):
        payload.recipient_bank_code = None
        payload.recipient_bank_code_provider = None
        payload.recipient_resolution_provider = None

    req_bank_name = str(payload.recipient_bank_name or "").strip()
    if req_bank_name and selected_bank_name and normalize_name(req_bank_name) == normalize_name(selected_bank_name):
        payload.recipient_bank_name = None


def compute_missing_recipient_fields(payload: TransferPayload) -> list[str]:
    required_fields: list[str] = []
    if not payload.recipient_account:
        required_fields.append("recipient_account")
    if not payload.recipient_bank_name and not payload.recipient_bank_code:
        required_fields.append("recipient_bank_name")
    return required_fields


def ask_account_and_bank_prompt(locale: str, recipient_name: str | None) -> str:
    fallback_name = sanitize_recipient_display_name(recipient_name, locale)
    return render_message(
        "response.templates.ask_account_number_and_bank",
        locale,
        {"recipient_name": fallback_name},
    )
