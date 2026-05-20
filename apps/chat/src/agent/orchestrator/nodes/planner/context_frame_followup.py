"""Generic follow-up handling for frame-backed assistant responses."""

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from apps.chat.src.agent.graphs.__shared__.account_selection.reference import match_source_account_reference
from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.services.context_manager import OrchestratorContextManager
from shared.formatters.currency import format_naira_compact
from shared.types.planner import (
    ContextFrameFollowupDecision,
    ContextFrameFollowupFilters,
    ContextFrameReplayModifier,
)

CONTEXT_READ_LIST_LIMIT = 5
CONTEXT_FRAME_FOLLOWUP_MIN_CONFIDENCE = 0.55
CONTEXT_FRAME_REPLAY_MODIFIER_MIN_CONFIDENCE = 0.72
_SENSITIVE_KEYS = {"pin", "otp", "password", "token", "secret"}
_LOOKUP_STOPWORDS = {
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
_SEARCHABLE_DATA_KEYS = (
    "alias",
    "name",
    "account_name",
    "bank_name",
    "bank",
    "category",
    "merchant",
    "network",
    "group_by",
    "group_key",
    "account_number",
    "account",
    "counterparty",
    "description",
    "direction",
    "recipient_name",
    "recipient_resolved_name",
    "reference",
    "status",
    "mandate_status",
    "is_default",
    "transaction_type",
    "type",
)
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "amount": ("amount",),
    "bank": ("bank_name", "bank", "recipient_bank_name", "source_bank_name"),
    "counterparty": ("counterparty", "recipient_resolved_name", "recipient_name", "merchant", "name"),
    "date": ("date", "created_at", "completed_at"),
    "network": ("network",),
    "phone": ("recipient_phone", "phone", "phone_number", "target_phone"),
    "reference": ("reference", "transaction_id", "idempotency_key"),
    "status": ("status", "provider_status", "final_status", "mandate_status"),
}
_RANKING_ALIASES = {
    "largest": "max",
    "highest": "max",
    "biggest": "max",
    "most": "max",
    "smallest": "min",
    "lowest": "min",
    "least": "min",
    "newest": "newest",
    "latest": "newest",
    "recent": "newest",
    "oldest": "oldest",
    "earliest": "oldest",
}
_REPLAY_AMOUNT_TOKEN_RE = re.compile(
    r"(?P<prefix>₦|ngn|naira)?\s*"
    r"(?P<amount>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<suffix>[km])?\b",
    re.IGNORECASE,
)
_REPLAY_AMOUNT_OVERRIDE_RE = re.compile(
    r"\b(?:but|with|for|at|instead)\b\s+(?:with\s+)?"
    r"(?P<token>(?:₦|ngn|naira)?\s*\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*[km]?"
    r"|(?:₦|ngn|naira)?\s*\d+(?:\.\d+)?\s*[km]?)\b|"
    r"\b(?:make|change|set|update)\s+(?:it|amount|this|that)?\s*(?:to\s+)?"
    r"(?P<edit_token>(?:₦|ngn|naira)?\s*\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*[km]?"
    r"|(?:₦|ngn|naira)?\s*\d+(?:\.\d+)?\s*[km]?)\b",
    re.IGNORECASE,
)
_REPLAY_SOURCE_ACCOUNT_RE = re.compile(
    r"\b(?:from|using|use|debit|charge|switch(?:\s+it)?\s+to|"
    r"change\s+source(?:\s+account)?\s+to|"
    r"source(?:\s+account)?(?:\s+as|\s+to|\s+is)?|"
    r"with(?:\s+my|\s+the)|"
    r"make\s+(?:e|am|it)\s+(?:from|use)|"
    r"lati(?:\s+inu)?|lo|daga|(?:yi\s+)?amfani\s+da|ta\s+hanyar|site\s+na|jiri)\s+"
    r"(?:my\s+|the\s+)?"
    r"(?P<source>[a-z0-9][a-z0-9 .&'()-]{0,80}?)"
    r"(?=\s+(?:instead|for|fun|domin|saboda|maka|with|narration|memo|note|"
    r"description|reason|akosile|bayani|bayanin|nkowa|and|but)\b|[.?!,;]|$)",
    re.IGNORECASE,
)
_REPLAY_NARRATION_RE = re.compile(
    r"\b(?:with\s+)?(?:narration|memo|note|description|reason|purpose|"
    r"akosile|bayani|bayanin|nkowa)\b\s*"
    r"(?:as|to|is|:)?\s*(?P<explicit>[^.?!;\n]{1,120})|"
    r"\b(?:for|fun|domin|saboda|maka)\s+(?P<for_note>[^.?!;\n]{1,120})",
    re.IGNORECASE,
)
_FILTER_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "transaction_type": ("task_type", "transaction_type", "type", "direction"),
    "status": ("status", "provider_status", "final_status", "mandate_status"),
    "direction": ("direction", "transaction_type", "type"),
    "bank": ("bank_name", "bank", "recipient_bank_name", "source_bank_name"),
    "counterparty": ("counterparty", "recipient_resolved_name", "recipient_name", "merchant", "name", "label"),
}
_TOKEN_ALIASES: dict[str, tuple[str, ...]] = {
    "gt": ("gtbank", "gt bank"),
    "gtb": ("gtbank",),
    "gtbank": ("gt bank", "gtb"),
}


@dataclass(frozen=True, slots=True)
class ContextFrameFollowupResponse:
    response: str | None = None
    semantic_path_shape: str = "context_frame_followup"
    recent_domain_focus: str | None = None
    context_frames: list[ContextFrame] | None = None
    tasks: dict[str, TaskSpec] | None = None
    waves: list[list[str]] | None = None


@dataclass(frozen=True, slots=True)
class SurfaceAnswerRequest:
    state: OrchestratorState
    text: str
    decision: ContextFrameFollowupDecision | None = None
    replay_modifier: ContextFrameReplayModifier | None = None
    locale: str = "en"


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower()).rstrip("?.!,")


def _lookup_tokens(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [token for token in tokens if len(token) > 1 and token not in _LOOKUP_STOPWORDS]


def _token_variants(token: str) -> tuple[str, ...]:
    return (token, *_TOKEN_ALIASES.get(token, ()))


def _token_matches_searchable(token: str, searchable: str) -> bool:
    return any(re.search(rf"\b{re.escape(variant)}\b", searchable) for variant in _token_variants(token))


def _semantic_tokens(text: str | None) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(token) > 1}


def _frame_noun(frame_type: ContextFrameType, *, plural: bool) -> str:
    if frame_type == ContextFrameType.BENEFICIARY_LIST:
        return "saved beneficiaries" if plural else "saved beneficiary"
    if frame_type == ContextFrameType.ACCOUNT_LIST:
        return "linked accounts" if plural else "linked account"
    if frame_type == ContextFrameType.TRANSACTION_LIST:
        return "transactions or results" if plural else "transaction or result"
    if frame_type == ContextFrameType.TRANSACTION_DETAIL:
        return "transactions" if plural else "transaction"
    if frame_type == ContextFrameType.RECEIPT:
        return "receipt"
    return "items" if plural else "item"


def _frame_domain(frame_type: ContextFrameType) -> str | None:
    if frame_type == ContextFrameType.BENEFICIARY_LIST:
        return "beneficiary"
    if frame_type == ContextFrameType.ACCOUNT_LIST:
        return "account"
    if frame_type in {ContextFrameType.TRANSACTION_LIST, ContextFrameType.TRANSACTION_DETAIL, ContextFrameType.RECEIPT}:
        return "query"
    return None


def _format_completeness_response(frame: ContextFrame) -> str | None:
    count = len(frame.items)
    if count <= 0:
        return None
    if count == 1:
        return f"Yes. That's the only {_frame_noun(frame.frame_type, plural=False)} I found."
    return f"Yes. Those are the {count} {_frame_noun(frame.frame_type, plural=True)} I found."


def _display_key(key: str) -> str:
    return key.replace("_", " ").strip().title()


def _candidate_detail_fields(entity: ContextEntity) -> list[tuple[str, Any]]:
    data = entity.data if isinstance(entity.data, dict) else {}
    if entity.entity_type.value == "beneficiary":
        keys = ("account_name", "name", "bank_name", "bank", "account_number", "account")
    elif entity.entity_type.value == "account":
        keys = ("bank_name", "account_number", "mandate_status", "available_balance", "balance")
    elif entity.entity_type.value == "transaction":
        keys = (
            "amount",
            "date",
            "counterparty",
            "description",
            "bank_name",
            "bank",
            "transaction_type",
            "type",
            "direction",
            "status",
            "category",
            "merchant",
            "network",
            "phone",
            "recipient_phone",
            "reference",
            "narration",
        )
    else:
        keys = (
            "summary",
            "description",
            "status",
            "amount",
            "count",
            "date",
            "bank_name",
            "bank",
            "group_by",
            "group_key",
            "category",
            "merchant",
            "network",
            "phone",
            "recipient_phone",
            "transaction_type",
            "type",
        )

    fields: list[tuple[str, Any]] = []
    for key in keys:
        if key in _SENSITIVE_KEYS:
            continue
        value = data.get(key)
        if value is None or value == "":
            continue
        fields.append((_display_key(key), value))
    return fields


def _format_detail_block(entity: ContextEntity, *, ordinal: int | None = None) -> str | None:
    header = entity.label or "Item"
    if ordinal is not None:
        header = f"{ordinal}. {header}"

    lines = [header]
    for label, value in _candidate_detail_fields(entity):
        lines.append(f"{label}: {value}")
    if len(lines) == 1:
        return None
    return "\n".join(lines)


def _requested_field_keys(requested_field: str | None) -> tuple[str, tuple[str, ...]] | None:
    if not requested_field:
        return None
    label = requested_field.strip().lower()
    keys = _FIELD_ALIASES.get(label)
    return (label, keys) if keys else None


def _decision_target_text(decision: ContextFrameFollowupDecision) -> str:
    return (decision.target_text or "").strip()


def _decision_field_text(decision: ContextFrameFollowupDecision) -> str | None:
    return decision.requested_field


def _decision_rank_text(decision: ContextFrameFollowupDecision) -> str | None:
    return decision.rank


def _has_filters(filters: ContextFrameFollowupFilters | None) -> bool:
    if filters is None:
        return False
    return any(
        bool(value)
        for value in (
            filters.transaction_type,
            filters.status,
            filters.direction,
            filters.bank,
            filters.counterparty,
        )
    )


def _value_matches_filter(value: Any, expected: str) -> bool:
    if value is None or value == "":
        return False
    expected_tokens = _lookup_tokens(expected) or list(_semantic_tokens(expected))
    if not expected_tokens:
        return False
    haystack = _normalize(str(value))
    return all(_token_matches_searchable(token, haystack) for token in expected_tokens)


def _entity_matches_filter(entity: ContextEntity, filter_name: str, expected: str) -> bool:
    data = entity.data if isinstance(entity.data, dict) else {}
    for key in _FILTER_FIELD_ALIASES[filter_name]:
        value = entity.label if key == "label" else data.get(key)
        if _value_matches_filter(value, expected):
            return True
    return False


def _find_filtered_entities(
    frame: ContextFrame,
    filters: ContextFrameFollowupFilters | None,
) -> list[ContextEntity]:
    if not _has_filters(filters) or filters is None:
        return []

    active_filters = {
        "transaction_type": filters.transaction_type,
        "status": filters.status,
        "direction": filters.direction,
        "bank": filters.bank,
        "counterparty": filters.counterparty,
    }
    matches: list[ContextEntity] = []
    for entity in frame.items:
        if all(
            _entity_matches_filter(entity, filter_name, expected)
            for filter_name, expected in active_filters.items()
            if expected
        ):
            matches.append(entity)
    return matches


def _entity_field_value(entity: ContextEntity, keys: tuple[str, ...]) -> Any:
    data = entity.data if isinstance(entity.data, dict) else {}
    for key in keys:
        value = data.get(key)
        if value is not None and value != "":
            return value
    return None


def _format_field_response(
    frame: ContextFrame,
    entities: list[ContextEntity],
    requested_field: str | None,
) -> str | None:
    field = _requested_field_keys(requested_field)
    if field is None or not entities:
        return None

    label, keys = field
    lines: list[str] = []
    for idx, entity in enumerate(entities[:CONTEXT_READ_LIST_LIMIT], 1):
        value = _entity_field_value(entity, keys)
        if value is None:
            continue
        prefix = f"{idx}. {entity.label}: " if len(entities) > 1 else ""
        lines.append(f"{prefix}{_display_key(label)}: {value}")

    if not lines:
        return None
    header = _detail_header(frame) if len(entities) > 1 else entity.label or _detail_header(frame)
    return f"{header}\n\n" + "\n".join(lines)


def _numeric_rank_value(entity: ContextEntity) -> float | None:
    data = entity.data if isinstance(entity.data, dict) else {}
    for key in ("amount", "count", "balance", "available_balance"):
        value = data.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            cleaned = re.sub(r"[^0-9.\-]", "", value)
            if cleaned:
                try:
                    return float(cleaned)
                except ValueError:
                    continue
    return None


def _amount_reference_values(text: str | None) -> set[float]:
    if not text:
        return set()

    values: set[float] = set()
    normalized = text.lower().replace(",", "")

    def _add(raw_number: str, suffix: str | None = None) -> None:
        try:
            value = float(raw_number)
        except ValueError:
            return
        if suffix == "k":
            value *= 1000
        elif suffix == "m":
            value *= 1_000_000
        values.add(value)

    for match in re.finditer(r"(?:₦|ngn|naira)\s*([0-9]+(?:\.[0-9]+)?)\s*([km])?\b", normalized):
        _add(match.group(1), match.group(2))

    for match in re.finditer(r"\b([0-9]+(?:\.[0-9]+)?)\s*([km])\b", normalized):
        _add(match.group(1), match.group(2))

    for match in re.finditer(r"\b[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?\b", text.lower()):
        _add(match.group(0).replace(",", ""))

    for match in re.finditer(r"\b[0-9]{4,}(?:\.[0-9]+)?\b", normalized):
        _add(match.group(0))

    return values


def _parse_replay_amount_token(token: str | None) -> float | None:
    if not token:
        return None

    match = _REPLAY_AMOUNT_TOKEN_RE.search(token)
    if not match:
        return None

    try:
        value = float(match.group("amount").replace(",", ""))
    except ValueError:
        return None

    suffix = (match.group("suffix") or "").lower()
    if suffix == "k":
        value *= 1000
    elif suffix == "m":
        value *= 1_000_000

    if value <= 0:
        return None

    has_explicit_money_marker = bool(match.group("prefix") or suffix or "," in match.group("amount"))
    if not has_explicit_money_marker and value > 100_000_000:
        return None

    return value


def _replay_amount_override(text: str | None) -> float | None:
    if not text:
        return None

    match = _REPLAY_AMOUNT_OVERRIDE_RE.search(text)
    if not match:
        return None
    token = match.group("token") or match.group("edit_token")
    return _parse_replay_amount_token(token)


def _contains_replay_modifier_evidence(text: str | None, evidence: str | None) -> bool:
    if not text or not evidence:
        return False

    normalized_text = re.sub(r"\s+", " ", text).strip().casefold()
    normalized_evidence = re.sub(r"\s+", " ", evidence).strip().casefold()
    if not normalized_evidence:
        return False
    return normalized_evidence in normalized_text


def _trusted_replay_modifier(modifier: ContextFrameReplayModifier | None) -> ContextFrameReplayModifier | None:
    if modifier is None:
        return None
    if modifier.confidence < CONTEXT_FRAME_REPLAY_MODIFIER_MIN_CONFIDENCE:
        return None
    return modifier


def _modifier_amount_override(text: str | None, modifier: ContextFrameReplayModifier | None) -> float | None:
    trusted = _trusted_replay_modifier(modifier)
    if trusted is None or trusted.amount is None:
        return None
    if not _contains_replay_modifier_evidence(text, trusted.amount_evidence):
        return None
    return trusted.amount if trusted.amount > 0 else None


def _modifier_source_account_candidate(
    text: str | None,
    modifier: ContextFrameReplayModifier | None,
) -> str | None:
    trusted = _trusted_replay_modifier(modifier)
    if trusted is None or not trusted.source_account_reference:
        return None
    if not _contains_replay_modifier_evidence(text, trusted.source_account_evidence):
        return None
    return trusted.source_account_reference.strip() or None


def _modifier_narration_candidate(
    text: str | None,
    modifier: ContextFrameReplayModifier | None,
) -> str | None:
    trusted = _trusted_replay_modifier(modifier)
    if trusted is None or not trusted.narration:
        return None
    if not _contains_replay_modifier_evidence(text, trusted.narration_evidence):
        return None
    return trusted.narration.strip() or None


def _clean_replay_modifier_text(value: str | None) -> str | None:
    if not value:
        return None

    cleaned = re.split(
        r"\b(?:from|using|use|debit|charge|switch(?:\s+it)?\s+to|"
        r"change\s+source(?:\s+account)?\s+to|with\s+(?:₦|ngn|naira|\d))\b",
        value,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    cleaned = re.split(
        r"\b(?:lati(?:\s+inu)?|lo|daga|(?:yi\s+)?amfani\s+da|ta\s+hanyar|site\s+na|jiri)\b",
        cleaned,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    cleaned = re.split(r"\b(?:instead|please|pls)\b", cleaned, maxsplit=1, flags=re.IGNORECASE)[0]
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" \"'.,;:!?")
    cleaned = re.sub(r"^(?:as|to|is|be)\s+", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned or None


def _replay_source_account_candidate(text: str | None) -> str | None:
    if not text:
        return None

    for match in _REPLAY_SOURCE_ACCOUNT_RE.finditer(text):
        candidate = _clean_replay_modifier_text(match.group("source"))
        if candidate:
            return candidate
    return None


def _loaded_accounts(state: OrchestratorState) -> list[dict[str, Any]]:
    loaded_context = state.loaded_context or {}
    account_sources = (
        loaded_context.get("transaction_accounts"),
        loaded_context.get("accounts"),
        loaded_context.get("all_accounts"),
    )
    accounts_by_key: dict[str, dict[str, Any]] = {}
    for account_source in account_sources:
        if not isinstance(account_source, list):
            continue
        for account in account_source:
            if not isinstance(account, dict):
                continue
            account_id = str(account.get("id") or account.get("account_id") or "").strip()
            bank_name = str(
                account.get("bank_name")
                or account.get("bank")
                or account.get("source_bank_name")
                or ""
            ).strip()
            account_number = str(
                account.get("account_number")
                or account.get("source_account_number")
                or ""
            ).strip()
            key = account_id or f"{bank_name}:{account_number}"
            if key and key not in accounts_by_key:
                accounts_by_key[key] = account
    return list(accounts_by_key.values())


def _source_account_patch(account: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_account_id": account.get("id") or account.get("account_id"),
        "source_bank_name": account.get("bank_name") or account.get("bank"),
        "source_account_name": account.get("account_name") or account.get("name"),
        "source_account_number": account.get("account_number") or account.get("source_account_number"),
        "source_affinity_mode": "explicit",
        "source_account_index": None,
        "funding_plan": None,
        "suggested_amount": None,
    }


def _replay_source_account_override(
    text: str | None,
    state: OrchestratorState,
    replay_modifier: ContextFrameReplayModifier | None,
) -> tuple[bool, dict[str, Any] | None, str | None]:
    candidate = _replay_source_account_candidate(text)
    if not candidate:
        candidate = _modifier_source_account_candidate(text, replay_modifier)
    if not candidate:
        return False, None, None

    matched_account = match_source_account_reference(candidate, _loaded_accounts(state))
    if not matched_account:
        return True, None, candidate

    return True, _source_account_patch(matched_account), candidate


def _text_is_replay_amount_token(value: str) -> bool:
    return bool(_REPLAY_AMOUNT_TOKEN_RE.fullmatch(value.strip()))


def _replay_narration_override(
    text: str | None,
    *,
    accounts: list[dict[str, Any]] | None = None,
    replay_modifier: ContextFrameReplayModifier | None = None,
) -> str | None:
    if not text:
        return None

    for match in _REPLAY_NARRATION_RE.finditer(text):
        is_bare_for = match.group("for_note") is not None
        candidate = _clean_replay_modifier_text(match.group("explicit") or match.group("for_note"))
        if not candidate:
            continue
        if is_bare_for:
            if _text_is_replay_amount_token(candidate):
                continue
            if accounts and match_source_account_reference(candidate, accounts):
                continue
        return candidate
    return _modifier_narration_candidate(text, replay_modifier)


def _entity_matches_amount_reference(entity: ContextEntity, amount_refs: set[float]) -> bool:
    if not amount_refs:
        return False
    value = _numeric_rank_value(entity)
    if value is None:
        return False
    abs_value = abs(value)
    return any(abs(abs_value - abs(target)) < 0.01 for target in amount_refs)


def _format_currency_amount(value: float) -> str:
    return format_naira_compact(value, absolute=True)


def _date_rank_value(entity: ContextEntity) -> float | None:
    data = entity.data if isinstance(entity.data, dict) else {}
    for key in ("date", "completed_at", "created_at"):
        value = data.get(key)
        if not value:
            continue
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            candidate = value.replace("Z", "+00:00")
            try:
                return datetime.fromisoformat(candidate).timestamp()
            except ValueError:
                continue
    return None


def _ranked_entity(frame: ContextFrame, rank_text: str | None) -> ContextEntity | None:
    tokens = _semantic_tokens(rank_text)
    rank_mode = next((_RANKING_ALIASES[token] for token in tokens if token in _RANKING_ALIASES), None)
    if rank_mode is None:
        return None

    if rank_mode in {"newest", "oldest"}:
        scored = [(entity, value) for entity in frame.items if (value := _date_rank_value(entity)) is not None]
        if not scored:
            return None
        if rank_mode == "newest":
            return max(scored, key=lambda item: item[1])[0]
        return min(scored, key=lambda item: item[1])[0]

    scored = [(entity, value) for entity in frame.items if (value := _numeric_rank_value(entity)) is not None]
    if not scored:
        return None
    return max(scored, key=lambda item: item[1])[0] if rank_mode == "max" else min(scored, key=lambda item: item[1])[0]


def _searchable_text(entity: ContextEntity) -> str:
    parts = [entity.label or ""]
    data = entity.data if isinstance(entity.data, dict) else {}
    for key in _SEARCHABLE_DATA_KEYS:
        value = data.get(key)
        if value is not None:
            parts.append(str(value))
    return _normalize(" ".join(parts))


def _find_matching_entities(frame: ContextFrame, lookup_query: str) -> list[ContextEntity]:
    query_tokens = _lookup_tokens(lookup_query)
    amount_refs = _amount_reference_values(lookup_query)
    if not query_tokens and not amount_refs:
        return []

    amount_matches = [entity for entity in frame.items if _entity_matches_amount_reference(entity, amount_refs)]
    if amount_matches:
        return amount_matches

    matches: list[ContextEntity] = []
    for entity in frame.items:
        searchable = _searchable_text(entity)
        if all(_token_matches_searchable(token, searchable) for token in query_tokens):
            matches.append(entity)
    if matches:
        return matches

    # Fall back to any-token match so short references like "tolu" can bind to a list
    # where the visible labels carry branch qualifiers.
    for entity in frame.items:
        searchable = _searchable_text(entity)
        if any(_token_matches_searchable(token, searchable) for token in query_tokens):
            matches.append(entity)
    return matches


def _account_status_grounded_entities(frame: ContextFrame, text: str) -> list[ContextEntity]:
    """Find account entities when text names the account and one of its visible statuses."""
    if frame.frame_type != ContextFrameType.ACCOUNT_LIST:
        return []

    tokens = set(_lookup_tokens(text)) | _semantic_tokens(text)
    if not tokens:
        return []

    matches: list[ContextEntity] = []
    for entity in frame.items:
        data = entity.data if isinstance(entity.data, dict) else {}
        status = str(data.get("mandate_status") or data.get("status") or "").strip().lower()
        if not status or not any(_token_matches_searchable(token, status) for token in tokens):
            continue

        identity_values = [
            entity.label or "",
            str(data.get("bank_name") or ""),
            str(data.get("bank") or ""),
            str(data.get("account_number") or ""),
        ]
        identity = _normalize(" ".join(identity_values))
        if any(_token_matches_searchable(token, identity) for token in tokens):
            matches.append(entity)
    return matches


def _pending_account_entities(frame: ContextFrame) -> list[ContextEntity]:
    if frame.frame_type != ContextFrameType.ACCOUNT_LIST:
        return []
    entities: list[ContextEntity] = []
    for entity in frame.items:
        data = entity.data if isinstance(entity.data, dict) else {}
        status = str(data.get("mandate_status") or data.get("status") or "").strip().lower()
        if status and status != "ready":
            entities.append(entity)
    return entities


def _detail_header(frame: ContextFrame) -> str:
    if frame.frame_type == ContextFrameType.BENEFICIARY_LIST:
        return "Saved Beneficiary Details" if len(frame.items) > 1 else "Beneficiary Details"
    if frame.frame_type == ContextFrameType.ACCOUNT_LIST:
        return "Linked Account Details" if len(frame.items) > 1 else "Account Details"
    if frame.frame_type in {ContextFrameType.TRANSACTION_LIST, ContextFrameType.TRANSACTION_DETAIL}:
        return "Transaction Details"
    if frame.frame_type == ContextFrameType.RECEIPT:
        return "Receipt Details"
    return "Details"


def _format_details_response(frame: ContextFrame, text: str) -> str | None:
    del text
    if len(frame.items) > 1:
        blocks: list[str] = []
        for idx, entity in enumerate(frame.items[:CONTEXT_READ_LIST_LIMIT], 1):
            block = _format_detail_block(entity, ordinal=idx)
            if block:
                blocks.append(block)
        if not blocks:
            return None
        overflow = len(frame.items) - len(blocks)
        suffix = (
            f"\n\nShowing {len(blocks)} of {len(frame.items)} {_frame_noun(frame.frame_type, plural=True)}."
            if overflow > 0
            else ""
        )
        return f"{_detail_header(frame)}\n\n" + "\n\n".join(blocks) + suffix

    block = _format_detail_block(frame.items[0])
    if block is None:
        return None
    return f"{_detail_header(frame)}\n\n" + block


def _format_entity_details(frame: ContextFrame, entities: list[ContextEntity]) -> str | None:
    if not entities:
        return None
    if len(entities) == 1:
        block = _format_detail_block(entities[0])
        if block is None:
            return None
        return f"{_detail_header(frame)}\n\n{block}"

    blocks: list[str] = []
    for idx, entity in enumerate(entities[:CONTEXT_READ_LIST_LIMIT], 1):
        block = _format_detail_block(entity, ordinal=idx)
        if block:
            blocks.append(block)
    if not blocks:
        return None
    overflow = len(entities) - len(blocks)
    suffix = f"\n\nShowing {len(blocks)} of {len(entities)} matches." if overflow > 0 else ""
    return f"{_detail_header(frame)}\n\n" + "\n\n".join(blocks) + suffix


def _format_lookup_response(frame: ContextFrame, lookup_query: str, *, explicit_lookup: bool) -> str | None:
    query_tokens = _lookup_tokens(lookup_query)
    if not query_tokens:
        return None

    matches = _find_matching_entities(frame, lookup_query)
    query_label = " ".join(query_tokens).title()

    if not matches:
        if not explicit_lookup:
            return None
        return f"I don't see {query_label} in the {_frame_noun(frame.frame_type, plural=True)} I showed."

    if len(matches) == 1:
        block = _format_detail_block(matches[0])
        if block is None:
            return f"Yes. {matches[0].label} is in the {_frame_noun(frame.frame_type, plural=True)} I showed."
        return f"{_detail_header(frame)}\n\n{block}"

    blocks: list[str] = []
    for idx, entity in enumerate(matches[:CONTEXT_READ_LIST_LIMIT], 1):
        block = _format_detail_block(entity, ordinal=idx)
        if block:
            blocks.append(block)
    if not blocks:
        labels = "\n".join(f"{idx}. {entity.label}" for idx, entity in enumerate(matches[:CONTEXT_READ_LIST_LIMIT], 1))
        return f"I found {len(matches)} matching {_frame_noun(frame.frame_type, plural=True)}:\n\n{labels}"

    overflow = len(matches) - len(blocks)
    suffix = f"\n\nShowing {len(blocks)} of {len(matches)} matches." if overflow > 0 else ""
    header = f"I found {len(matches)} matches in the {_frame_noun(frame.frame_type, plural=True)} I showed."
    return header + "\n\n" + "\n\n".join(blocks) + suffix


def _format_selection_response(frame: ContextFrame, decision: ContextFrameFollowupDecision) -> str | None:
    if decision.selection_index is not None:
        idx = decision.selection_index - 1
        if 0 <= idx < len(frame.items):
            field_response = _format_field_response(frame, [frame.items[idx]], _decision_field_text(decision))
            return field_response or _format_entity_details(frame, [frame.items[idx]])

    target_text = _decision_target_text(decision)
    if target_text:
        ranked = _ranked_entity(frame, _decision_rank_text(decision))
        if ranked is not None:
            return _format_entity_details(frame, [ranked])
        return _format_lookup_response(frame, target_text, explicit_lookup=True)
    return None


def _canonical_decision(decision: str) -> str:
    aliases = {
        "completeness_check": "answer_completeness",
        "entity_lookup": "lookup_entity",
        "detail_request": "show_details",
        "selection": "select_item",
        "replay": "replay_tasks",
        "new_task": "start_new_task",
    }
    return aliases.get(decision, decision)


def _format_filter_response(
    frame: ContextFrame,
    target_text: str,
    *,
    rank_text: str | None = None,
    filters: ContextFrameFollowupFilters | None = None,
) -> str | None:
    ranked = _ranked_entity(frame, rank_text)
    if ranked is not None:
        return _format_entity_details(frame, [ranked])

    matches = _find_filtered_entities(frame, filters)
    if matches:
        return _format_entity_details(frame, matches)

    matches = _find_matching_entities(frame, target_text)
    if not matches:
        query_label = " ".join(_lookup_tokens(target_text)).title()
        if not query_label and filters is not None:
            query_label = " ".join(
                str(value).strip()
                for value in (
                    filters.transaction_type,
                    filters.status,
                    filters.direction,
                    filters.bank,
                    filters.counterparty,
                )
                if value
            ).title()
        if not query_label:
            return None
        return f"I don't see {query_label} in the {_frame_noun(frame.frame_type, plural=True)} I showed."
    return _format_entity_details(frame, matches)


def _format_compare_response(
    frame: ContextFrame,
    target_text: str | None,
    *,
    rank_text: str | None = None,
    filters: ContextFrameFollowupFilters | None = None,
) -> str | None:
    ranked = _ranked_entity(frame, rank_text)
    if ranked is not None:
        return _format_entity_details(frame, [ranked])

    entities = _find_filtered_entities(frame, filters)
    if not entities:
        entities = _find_matching_entities(frame, target_text) if target_text else frame.items
    if len(entities) < 2:
        entities = frame.items
    if len(entities) < 2:
        return _format_entity_details(frame, entities)

    blocks: list[str] = []
    for idx, entity in enumerate(entities[:CONTEXT_READ_LIST_LIMIT], 1):
        fields = _candidate_detail_fields(entity)
        if not fields:
            blocks.append(f"{idx}. {entity.label}")
            continue
        lines = [f"{idx}. {entity.label}"]
        for label, value in fields:
            lines.append(f"{label}: {value}")
        blocks.append("\n".join(lines))

    if not blocks:
        return None
    overflow = len(entities) - len(blocks)
    suffix = f"\n\nShowing {len(blocks)} of {len(entities)} items." if overflow > 0 else ""
    return "Comparison\n\n" + "\n\n".join(blocks) + suffix


def _format_account_status_explanation(entity: ContextEntity) -> str | None:
    data = entity.data if isinstance(entity.data, dict) else {}
    status = str(data.get("mandate_status") or data.get("status") or "").strip()
    if not status:
        return None

    label = entity.label or str(data.get("bank_name") or "This account")
    normalized_status = status.lower()
    if normalized_status in {"pending", "awaiting_authorization"}:
        readable_status = "pending" if normalized_status == "pending" else "awaiting authorization"
        lines = [
            f"{label} is still {readable_status} because the account authorization is not complete yet.",
            "",
            f"Mandate Status: {status}",
        ]
        lines.extend(["", _format_account_status_instruction(normalized_status, data)])
        return "\n".join(lines)
    if normalized_status == "approved":
        return (
            f"{label} authorization has been approved, but the account is still waiting for final readiness checks.\n\n"
            f"Mandate Status: {status}\n\n"
            f"{_format_account_status_instruction(normalized_status, data)}"
        )
    if normalized_status == "ready":
        return (
            f"{label} is ready for transactions.\n\n"
            "Mandate Status: ready\n\n"
            f"{_format_account_status_instruction(normalized_status, data)}"
        )
    if normalized_status == "rejected":
        return (
            f"{label} authorization was rejected. "
            "You may need to restart account authorization or relink the account.\n\n"
            f"Mandate Status: {status}\n\n"
            f"{_format_account_status_instruction(normalized_status, data)}"
        )
    if normalized_status == "cancelled":
        return (
            f"{label} authorization was cancelled. Relink or reauthorize the account to use it for transactions.\n\n"
            f"Mandate Status: {status}\n\n"
            f"{_format_account_status_instruction(normalized_status, data)}"
        )
    if normalized_status == "expired":
        return (
            f"{label} authorization expired before completion. Restart account authorization to activate it.\n\n"
            f"Mandate Status: {status}\n\n"
            f"{_format_account_status_instruction(normalized_status, data)}"
        )
    if normalized_status == "paused":
        return (
            f"{label} authorization is paused.\n\n"
            f"Mandate Status: {status}\n\n"
            f"{_format_account_status_instruction(normalized_status, data)}"
        )
    return f"{label} has mandate status: {status}."


def _account_extra_data(data: dict[str, Any]) -> dict[str, Any]:
    extra_data = data.get("extra_data")
    if isinstance(extra_data, dict):
        return extra_data
    if isinstance(extra_data, str):
        try:
            parsed = json.loads(extra_data)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _account_transfer_destinations(data: dict[str, Any]) -> list[dict[str, str]]:
    raw = data.get("transfer_destinations")
    if not isinstance(raw, list):
        raw = _account_extra_data(data).get("transfer_destinations")
    if not isinstance(raw, list):
        return []

    destinations: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        bank_name = str(item.get("bank_name") or "").strip()
        account_number = str(item.get("account_number") or "").strip()
        if bank_name and account_number:
            destinations.append({"bank_name": bank_name, "account_number": account_number})
    return destinations


def _format_pending_account_completion_steps(data: dict[str, Any]) -> str:
    bank_name = str(data.get("bank_name") or data.get("bank") or "the pending account").strip()
    account_number = str(data.get("account_number") or data.get("number") or "").strip()
    suffix = f" ending in {account_number[-4:]}" if account_number else ""
    destinations = _account_transfer_destinations(data)

    if not destinations:
        return (
            f"To complete it, make the ₦50 authorization transfer from your {bank_name} account{suffix}. "
            "Once the bank/NIBSS confirms it, the account becomes ready."
        )

    lines = [f"To complete it, transfer ₦50 from your {bank_name} account{suffix} to any of these accounts:"]
    for destination in destinations:
        lines.append(f"• {destination['bank_name']}: {destination['account_number']}")
    lines.append("Once the bank/NIBSS confirms it, the account becomes ready.")
    return "\n".join(lines)


def _format_account_status_instruction(normalized_status: str, data: dict[str, Any]) -> str:
    if normalized_status in {"pending", "awaiting_authorization"}:
        return _format_pending_account_completion_steps(data)
    if normalized_status == "approved":
        return (
            "Next step: wait for NIBSS/bank verification. "
            "This usually takes a few minutes but can take up to 24 hours."
        )
    if normalized_status == "ready":
        return "Next step: no action needed. You can use this account for payments."
    if normalized_status == "rejected":
        return "Next step: contact support or restart account authorization before using this account for payments."
    if normalized_status == "cancelled":
        return "Next step: reinitiate account authorization or relink the account."
    if normalized_status == "expired":
        return "Next step: restart account authorization; the previous authorization window has expired."
    if normalized_status == "paused":
        return "Next step: contact support to reinstate this account authorization."
    return ""


def _format_explain_result_response(
    frame: ContextFrame,
    decision: ContextFrameFollowupDecision | None = None,
    *,
    text: str = "",
) -> str | None:
    if decision is not None:
        entities: list[ContextEntity] = []
        if decision.selection_index is not None:
            idx = decision.selection_index - 1
            if 0 <= idx < len(frame.items):
                entities = [frame.items[idx]]
        if not entities and _has_filters(decision.filters):
            entities = _find_filtered_entities(frame, decision.filters)
        if not entities and _decision_target_text(decision):
            entities = _find_matching_entities(frame, _decision_target_text(decision))
        if not entities and text:
            entities = _account_status_grounded_entities(frame, text)
        if not entities and frame.frame_type == ContextFrameType.ACCOUNT_LIST:
            pending_accounts = _pending_account_entities(frame)
            if len(pending_accounts) == 1:
                entities = pending_accounts

        if len(entities) == 1 and frame.frame_type == ContextFrameType.ACCOUNT_LIST:
            explanation = _format_account_status_explanation(entities[0])
            if explanation:
                return explanation
        if entities:
            field_response = _format_field_response(frame, entities, _decision_field_text(decision))
            return field_response or _format_entity_details(frame, entities)

    count = len(frame.items)
    if count <= 0:
        return None

    noun = _frame_noun(frame.frame_type, plural=count != 1)
    labels = [entity.label for entity in frame.items[:CONTEXT_READ_LIST_LIMIT] if entity.label]
    if not labels:
        return f"I showed {count} {noun} from the last result."

    label_text = ", ".join(labels)
    overflow = count - len(labels)
    suffix = f", and {overflow} more" if overflow > 0 else ""
    return f"I showed {count} {noun}: {label_text}{suffix}."


def _format_frame_clarification_response(frame: ContextFrame) -> str | None:
    domain = _frame_domain(frame.frame_type)
    if domain == "beneficiary":
        return "Are you asking about the saved beneficiaries I just showed?"
    if domain == "account":
        return "Are you asking about the linked accounts I just showed?"
    if domain == "query":
        return "Are you asking about the result I just showed?"
    return "Are you asking about the items I just showed?"


def _format_unclear_grounded_target_response(frame: ContextFrame, text: str) -> str | None:
    amount_refs = _amount_reference_values(text)
    matches = _find_matching_entities(frame, text)
    if len(matches) == 1:
        return _format_entity_details(frame, matches)
    if len(matches) > 1:
        return _format_entity_details(frame, matches)
    if amount_refs:
        amounts = ", ".join(_format_currency_amount(value) for value in sorted(amount_refs))
        return f"I don't see {amounts} in the {_frame_noun(frame.frame_type, plural=True)} I showed."
    return None


def _format_missing_amount_reference_response(frame: ContextFrame, text: str | None) -> str | None:
    amount_refs = _amount_reference_values(text)
    if not amount_refs:
        return None
    amounts = ", ".join(_format_currency_amount(value) for value in sorted(amount_refs))
    return f"I don't see {amounts} in the {_frame_noun(frame.frame_type, plural=True)} I showed."


def _format_semantic_decision_response(
    frame: ContextFrame,
    decision: ContextFrameFollowupDecision,
    *,
    text: str = "",
) -> str | None:
    if decision.confidence < CONTEXT_FRAME_FOLLOWUP_MIN_CONFIDENCE:
        return None

    semantic_decision = _canonical_decision(decision.decision)

    if semantic_decision == "start_new_task":
        status_matches = _account_status_grounded_entities(frame, text)
        if len(status_matches) == 1:
            explanation = _format_account_status_explanation(status_matches[0])
            if explanation:
                return explanation
        return None

    if semantic_decision == "replay_tasks":
        return None

    if semantic_decision == "answer_completeness":
        return _format_completeness_response(frame)

    if semantic_decision == "show_details":
        target_text = _decision_target_text(decision)
        field_text = _decision_field_text(decision)
        rank_text = _decision_rank_text(decision)
        if decision.selection_index is not None:
            idx = decision.selection_index - 1
            if 0 <= idx < len(frame.items):
                field_response = _format_field_response(frame, [frame.items[idx]], field_text)
                return field_response or _format_entity_details(frame, [frame.items[idx]])
        if rank_text:
            ranked = _ranked_entity(frame, rank_text)
            if ranked is not None:
                return _format_entity_details(frame, [ranked])
        if target_text:
            matches = _find_matching_entities(frame, target_text)
            if matches:
                field_response = _format_field_response(frame, matches, field_text)
                return field_response or _format_entity_details(frame, matches)
            missing_amount_response = _format_missing_amount_reference_response(frame, target_text)
            if missing_amount_response:
                return missing_amount_response
        matches = _find_filtered_entities(frame, decision.filters)
        if matches:
            field_response = _format_field_response(frame, matches, field_text)
            return field_response or _format_entity_details(frame, matches)
        field_response = _format_field_response(frame, frame.items, field_text)
        if field_response:
            return field_response
        return _format_details_response(frame, "details")

    if semantic_decision == "lookup_entity":
        target_text = _decision_target_text(decision)
        if not target_text:
            return _format_frame_clarification_response(frame)
        return _format_lookup_response(frame, target_text, explicit_lookup=True)

    if semantic_decision == "filter_items":
        target_text = _decision_target_text(decision)
        if not target_text and not decision.rank and not _has_filters(decision.filters):
            return _format_frame_clarification_response(frame)
        return _format_filter_response(
            frame,
            target_text,
            rank_text=_decision_rank_text(decision),
            filters=decision.filters,
        )

    if semantic_decision == "compare_items":
        return _format_compare_response(
            frame,
            _decision_target_text(decision) or None,
            rank_text=_decision_rank_text(decision),
            filters=decision.filters,
        )

    if semantic_decision == "select_item":
        return _format_selection_response(frame, decision)

    if semantic_decision == "explain_result":
        return _format_explain_result_response(frame, decision, text=text)

    if semantic_decision == "unclear" and decision.confidence >= CONTEXT_FRAME_FOLLOWUP_MIN_CONFIDENCE:
        grounded_target = _format_unclear_grounded_target_response(frame, text)
        if grounded_target:
            return grounded_target
        return _format_frame_clarification_response(frame)

    return None


def _active_context_frames(state: OrchestratorState) -> list[ContextFrame]:
    now = int(time.time())
    return [
        frame
        for frame in state.context_frames
        if frame.items and (frame.created_at_ts + frame.ttl_seconds) > now
    ]


def _decision_has_entity_match(frame: ContextFrame, decision: ContextFrameFollowupDecision) -> bool:
    if decision.selection_index is not None:
        idx = decision.selection_index - 1
        return 0 <= idx < len(frame.items)

    if _ranked_entity(frame, _decision_rank_text(decision)) is not None:
        return True

    if _find_filtered_entities(frame, decision.filters):
        return True

    target_text = _decision_target_text(decision)
    return bool(target_text and _find_matching_entities(frame, target_text))


def _frame_supports_decision(frame: ContextFrame, decision: ContextFrameFollowupDecision) -> bool:
    semantic_decision = _canonical_decision(decision.decision)
    if semantic_decision in {"start_new_task", "unclear", "answer_completeness", "compare_items", "replay_tasks"}:
        return True
    if semantic_decision in {"show_details", "select_item", "filter_items", "lookup_entity", "explain_result"}:
        if _decision_has_entity_match(frame, decision):
            return True
        if (
            semantic_decision == "show_details"
            and decision.requested_field
            and len(frame.items) == 1
            and frame.frame_type in {ContextFrameType.TRANSACTION_DETAIL, ContextFrameType.RECEIPT}
        ):
            return True
    return False


def _select_frame_for_decision(state: OrchestratorState, decision: ContextFrameFollowupDecision) -> ContextFrame | None:
    active_frames = _active_context_frames(state)
    if not active_frames:
        return None

    latest = active_frames[-1]
    if _frame_supports_decision(latest, decision):
        return latest

    latest_domain = _frame_domain(latest.frame_type)
    for frame in reversed(active_frames[:-1]):
        if latest_domain and _frame_domain(frame.frame_type) != latest_domain:
            continue
        if _frame_supports_decision(frame, decision):
            return frame
    return latest


def _decision_with_grounding_hints(
    decision: ContextFrameFollowupDecision,
    text: str,
) -> ContextFrameFollowupDecision:
    """Promote obvious visible references from raw text when the classifier omitted them."""
    semantic_decision = _canonical_decision(decision.decision)
    if semantic_decision not in {"show_details", "select_item", "filter_items", "lookup_entity", "explain_result"}:
        return decision
    if _decision_target_text(decision):
        return decision
    if not _amount_reference_values(text):
        return decision
    return decision.model_copy(update={"target_text": text})


def _transaction_task_type(entity: ContextEntity) -> str:
    data = entity.data if isinstance(entity.data, dict) else {}
    payload_type = ""
    if entity.selection_payload is not None:
        payload_type = str(entity.selection_payload.entity_type or "")
    raw_type = data.get("task_type") or data.get("transaction_type") or data.get("type") or payload_type
    return str(raw_type).strip().lower()


def _replay_action(task_type: str) -> str:
    return {"transfer": "send_money", "airtime": "buy_airtime", "data": "buy_data"}[task_type]


def _new_replay_task_id(state: OrchestratorState, task_type: str, allocated_ids: set[str]) -> str:
    seen = set(state.tasks.keys()) | allocated_ids
    idx = 1
    task_id = f"context_replay_{task_type}_{idx}"
    while task_id in seen:
        idx += 1
        task_id = f"context_replay_{task_type}_{idx}"
    return task_id


def _payload_value(entity: ContextEntity, *keys: str) -> Any:
    data = entity.data if isinstance(entity.data, dict) else {}
    handoff = entity.selection_payload.handoff_payload if entity.selection_payload is not None else None
    sources = [handoff if isinstance(handoff, dict) else {}, data]
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value is not None and value != "":
                return value
    return None


def _base_replay_payload(
    entity: ContextEntity,
    *,
    task_type: str,
    text: str,
    replay_modifier: ContextFrameReplayModifier | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": _replay_action(task_type),
        "instruction": text,
        "message": text,
        "skip_extraction": True,
        "confirmation": {"confirmed": False},
        "idempotency_key": None,
        "transaction_id": None,
    }
    amount = _payload_value(entity, "amount")
    if amount is not None:
        payload["amount"] = amount
    amount_override = _replay_amount_override(text)
    if amount_override is None:
        amount_override = _modifier_amount_override(text, replay_modifier)
    if amount_override is not None:
        payload["amount"] = amount_override
        payload["suggested_amount"] = None
        payload["transfer_percentage"] = None
        payload["transfer_all"] = False
        payload["funding_plan"] = None

    for key in ("source_account_id", "source_bank_name", "source_account_number", "source_account_index"):
        value = _payload_value(entity, key)
        if value is not None:
            payload[key] = value
    return payload


def _replay_payload_for_entity(
    entity: ContextEntity,
    *,
    text: str,
    replay_modifier: ContextFrameReplayModifier | None,
) -> tuple[str, dict[str, Any]] | None:
    task_type = _transaction_task_type(entity)
    if task_type not in {"transfer", "airtime", "data"}:
        return None

    payload = _base_replay_payload(entity, task_type=task_type, text=text, replay_modifier=replay_modifier)
    if task_type == "transfer":
        for key in (
            "beneficiary_id",
            "recipient_name",
            "recipient_resolved_name",
            "recipient_account",
            "recipient_bank_name",
            "recipient_bank_code",
            "narration",
        ):
            value = _payload_value(entity, key)
            if value is not None:
                payload[key] = value
        if payload.get("amount") is None:
            return None
        if not any(payload.get(key) for key in ("beneficiary_id", "recipient_account", "recipient_name")):
            return None
        return task_type, payload

    if task_type == "airtime":
        phone = _payload_value(entity, "recipient_phone", "phone_number", "phone", "target_phone")
        network = _payload_value(entity, "network")
        if payload.get("amount") is None or not phone:
            return None
        payload["recipient_phone"] = phone
        payload["phone_number"] = phone
        if network:
            payload["network"] = network
        return task_type, payload

    phone = _payload_value(entity, "target_phone", "recipient_phone", "phone_number", "phone")
    if not phone:
        return None
    payload["target_phone"] = phone
    for key in ("network", "plan_code", "plan_name"):
        value = _payload_value(entity, key)
        if value is not None:
            payload[key] = value
    if payload.get("amount") is None and not (payload.get("plan_code") or payload.get("plan_name")):
        return None
    return task_type, payload


def _enrich_replay_source_account(payload: dict[str, Any], state: OrchestratorState) -> None:
    if payload.get("source_account_number"):
        return

    source_account_id = str(payload.get("source_account_id") or "").strip()
    source_bank_name = str(payload.get("source_bank_name") or "").strip().casefold()
    matched_account: dict[str, Any] | None = None
    for account in _loaded_accounts(state):
        account_id = str(
            account.get("id") or account.get("account_id") or account.get("source_account_id") or ""
        ).strip()
        bank_name = str(
            account.get("bank_name") or account.get("bank") or account.get("source_bank_name") or ""
        ).strip().casefold()
        if source_account_id and account_id == source_account_id:
            matched_account = account
            break
        if source_bank_name and bank_name == source_bank_name:
            matched_account = account
            break

    if not matched_account:
        return

    if not payload.get("source_account_id"):
        source_id = (
            matched_account.get("id")
            or matched_account.get("account_id")
            or matched_account.get("source_account_id")
        )
        if source_id:
            payload["source_account_id"] = source_id
    if not payload.get("source_bank_name"):
        bank_name = (
            matched_account.get("bank_name")
            or matched_account.get("bank")
            or matched_account.get("source_bank_name")
        )
        if bank_name:
            payload["source_bank_name"] = bank_name
    account_number = (
        matched_account.get("account_number")
        or matched_account.get("number")
        or matched_account.get("source_account_number")
    )
    if account_number:
        payload["source_account_number"] = account_number


def _apply_replay_narration_override(
    payload: dict[str, Any],
    text: str,
    state: OrchestratorState,
    replay_modifier: ContextFrameReplayModifier | None,
) -> None:
    narration = _replay_narration_override(
        text,
        accounts=_loaded_accounts(state),
        replay_modifier=replay_modifier,
    )
    if not narration:
        return

    payload["narration"] = narration
    payload["authored_narration"] = narration
    payload["user_note"] = narration


def _target_text_is_replay_amount_override(
    decision: ContextFrameFollowupDecision,
    text: str,
    replay_modifier: ContextFrameReplayModifier | None,
) -> bool:
    target_text = _decision_target_text(decision)
    if not target_text:
        return False

    override = _replay_amount_override(text)
    if override is None:
        override = _modifier_amount_override(text, replay_modifier)
    if override is None:
        return False

    target_amounts = _amount_reference_values(target_text)
    return len(target_amounts) == 1 and any(abs(amount - override) < 0.01 for amount in target_amounts)


def _modifier_matches_target_text(modifier: str | None, target_text: str) -> bool:
    if not modifier:
        return False

    normalized_modifier = _normalize(modifier)
    normalized_target = _normalize(target_text)
    if not normalized_modifier or not normalized_target:
        return False
    if normalized_modifier == normalized_target:
        return True
    if normalized_modifier in normalized_target or normalized_target in normalized_modifier:
        return True

    compact_modifier = normalized_modifier.replace(" ", "")
    compact_target = normalized_target.replace(" ", "")
    return bool(compact_modifier and compact_target and (compact_modifier == compact_target))


def _target_text_is_replay_modifier(
    decision: ContextFrameFollowupDecision,
    text: str,
    state: OrchestratorState,
    replay_modifier: ContextFrameReplayModifier | None,
) -> bool:
    target_text = _decision_target_text(decision)
    if not target_text:
        return False

    if _target_text_is_replay_amount_override(decision, text, replay_modifier):
        return True

    if _modifier_matches_target_text(_replay_source_account_candidate(text), target_text):
        return True
    if _modifier_matches_target_text(_modifier_source_account_candidate(text, replay_modifier), target_text):
        return True

    narration = _replay_narration_override(
        text,
        accounts=_loaded_accounts(state),
        replay_modifier=replay_modifier,
    )
    return _modifier_matches_target_text(narration, target_text)


def _replay_target_entities(
    frame: ContextFrame,
    decision: ContextFrameFollowupDecision,
    *,
    text: str,
    state: OrchestratorState,
    replay_modifier: ContextFrameReplayModifier | None,
) -> list[ContextEntity]:
    if decision.selection_index is not None:
        idx = decision.selection_index - 1
        if 0 <= idx < len(frame.items):
            return [frame.items[idx]]
        return []

    target_text = _decision_target_text(decision)
    if target_text:
        matches = _find_matching_entities(frame, target_text)
        if matches or not _target_text_is_replay_modifier(decision, text, state, replay_modifier):
            return matches

    filtered = _find_filtered_entities(frame, decision.filters)
    if filtered:
        return filtered

    return list(frame.items)


def _build_replay_response(
    state: OrchestratorState,
    frame: ContextFrame,
    decision: ContextFrameFollowupDecision,
    text: str,
    replay_modifier: ContextFrameReplayModifier | None = None,
) -> ContextFrameFollowupResponse | None:
    if frame.frame_type not in {
        ContextFrameType.TRANSACTION_LIST,
        ContextFrameType.TRANSACTION_DETAIL,
        ContextFrameType.RECEIPT,
    }:
        return None

    source_requested, source_patch, requested_source = _replay_source_account_override(text, state, replay_modifier)
    if source_requested and source_patch is None:
        source_label = requested_source or "that source account"
        return ContextFrameFollowupResponse(
            response=(
                f"I could not find {source_label!r} among your linked source accounts. "
                "Choose one of your linked accounts and try again."
            ),
            semantic_path_shape="context_frame_replay_source_unmatched",
            recent_domain_focus="transaction",
            context_frames=_refresh_context_frame(state, frame),
        )

    entities = _replay_target_entities(
        frame,
        decision,
        text=text,
        state=state,
        replay_modifier=replay_modifier,
    )
    tasks: dict[str, TaskSpec] = {}
    wave_ids: list[str] = []
    allocated_ids: set[str] = set()
    for entity in entities:
        replay_payload = _replay_payload_for_entity(entity, text=text, replay_modifier=replay_modifier)
        if replay_payload is None:
            continue
        task_type, payload = replay_payload
        _enrich_replay_source_account(payload, state)
        if source_patch is not None:
            payload.update(source_patch)
        if task_type == "transfer":
            _apply_replay_narration_override(payload, text, state, replay_modifier)
        task_id = _new_replay_task_id(state, task_type, allocated_ids)
        allocated_ids.add(task_id)
        tasks[task_id] = TaskSpec(id=task_id, type=cast(Any, task_type), stage=TaskStage.DRAFT, payload=payload)
        wave_ids.append(task_id)

    if not wave_ids:
        return None

    return ContextFrameFollowupResponse(
        semantic_path_shape="context_frame_replay",
        recent_domain_focus="transaction",
        context_frames=_refresh_context_frame(state, frame),
        tasks=tasks,
        waves=[wave_ids],
    )


def _single_focus_entity_for_decision(
    frame: ContextFrame,
    decision: ContextFrameFollowupDecision,
) -> ContextEntity | None:
    semantic_decision = _canonical_decision(decision.decision)
    if semantic_decision not in {"show_details", "select_item", "filter_items", "lookup_entity", "explain_result"}:
        return None

    if decision.selection_index is not None:
        idx = decision.selection_index - 1
        if 0 <= idx < len(frame.items):
            return frame.items[idx]
        return None

    ranked = _ranked_entity(frame, _decision_rank_text(decision))
    if ranked is not None:
        return ranked

    matches = _find_filtered_entities(frame, decision.filters)
    if len(matches) == 1:
        return matches[0]

    target_text = _decision_target_text(decision)
    if target_text:
        matches = _find_matching_entities(frame, target_text)
        if len(matches) == 1:
            return matches[0]

    if len(frame.items) == 1:
        return frame.items[0]
    return None


def _detail_frame_type_for_focus(frame: ContextFrame) -> ContextFrameType | None:
    if frame.frame_type in {ContextFrameType.TRANSACTION_LIST, ContextFrameType.RECEIPT}:
        return ContextFrameType.TRANSACTION_DETAIL
    return None


def _append_focus_detail_frame(
    frames: list[ContextFrame],
    source_frame: ContextFrame,
    decision: ContextFrameFollowupDecision,
) -> list[ContextFrame]:
    detail_type = _detail_frame_type_for_focus(source_frame)
    if detail_type is None:
        return frames

    entity = _single_focus_entity_for_decision(source_frame, decision)
    if entity is None:
        return frames

    now = int(time.time())
    detail_frame = ContextFrame(
        frame_id=f"{source_frame.frame_id}:focus:{entity.entity_id or now}",
        frame_type=detail_type,
        items=[entity.model_copy(deep=True)],
        focus_index=0,
        source_message_id=source_frame.source_message_id,
        created_at_ts=now,
        ttl_seconds=source_frame.ttl_seconds,
    )
    return [*frames, detail_frame][-OrchestratorContextManager().max_frames :]


def _context_frames_after_surface_answer(
    state: OrchestratorState,
    frame: ContextFrame,
    decision: ContextFrameFollowupDecision,
) -> list[ContextFrame]:
    refreshed = _refresh_context_frame(state, frame)
    return _append_focus_detail_frame(refreshed, frame, decision)


def _refresh_context_frame(state: OrchestratorState, active_frame: ContextFrame) -> list[ContextFrame]:
    now = int(time.time())
    refreshed: list[ContextFrame] = []
    for frame in state.context_frames:
        if frame.frame_id == active_frame.frame_id:
            refreshed.append(frame.model_copy(update={"created_at_ts": now}))
        elif (frame.created_at_ts + frame.ttl_seconds) > now:
            refreshed.append(frame)
    return refreshed


class SurfaceAnswerEngine:
    """Ground conversational follow-ups against the latest displayed frame."""

    def build_context(self, frame: ContextFrame) -> str:
        """Build a compact LLM context for interpreting frame follow-ups."""
        lines = [f"Frame type: {frame.frame_type.value}", f"Item count: {len(frame.items)}", "Items:"]
        for idx, entity in enumerate(frame.items[:CONTEXT_READ_LIST_LIMIT], 1):
            data = entity.data if isinstance(entity.data, dict) else {}
            searchable_values = []
            for key in _SEARCHABLE_DATA_KEYS:
                value = data.get(key)
                if value is not None and value != "":
                    searchable_values.append(f"{key}={value}")
            suffix = f" | {'; '.join(searchable_values[:6])}" if searchable_values else ""
            lines.append(f"{idx}. {entity.label}{suffix}")
        overflow = len(frame.items) - CONTEXT_READ_LIST_LIMIT
        if overflow > 0:
            lines.append(f"... {overflow} more item(s) not shown in interpreter context")
        return "\n".join(lines)

    def build_state_context(self, state: OrchestratorState) -> str:
        """Build LLM context from active frames, preserving current focus and prior lists."""
        frames = _active_context_frames(state)
        if not frames:
            return ""
        if len(frames) == 1:
            return self.build_context(frames[-1])

        selected_frames = list(reversed(frames[-3:]))
        lines = [
            "Active displayed context, most recent first.",
            "Use the current focus for pronouns like this/that.",
            "Use an earlier list when the user refers to a visible item number not present in the current focus.",
        ]
        for idx, frame in enumerate(selected_frames):
            title = "Current focus" if idx == 0 else f"Earlier result {idx}"
            lines.append("")
            lines.append(f"{title}:")
            lines.append(self.build_context(frame))
        return "\n".join(lines)

    def answer(self, request: SurfaceAnswerRequest) -> ContextFrameFollowupResponse | None:
        """Answer a grounded follow-up from current state and a typed decision."""
        decision = (
            _decision_with_grounding_hints(request.decision, request.text)
            if request.decision is not None
            else None
        )
        frame = _select_frame_for_decision(request.state, decision) if decision is not None else None
        if frame is None or not frame.items or decision is None:
            return None

        if _canonical_decision(decision.decision) == "replay_tasks":
            if decision.confidence < CONTEXT_FRAME_FOLLOWUP_MIN_CONFIDENCE:
                return None
            return _build_replay_response(
                request.state,
                frame,
                decision,
                request.text,
                replay_modifier=request.replay_modifier,
            )

        response = _format_semantic_decision_response(frame, decision, text=request.text)
        if not response:
            return None
        return ContextFrameFollowupResponse(
            response=response,
            recent_domain_focus=_frame_domain(frame.frame_type),
            context_frames=_context_frames_after_surface_answer(request.state, frame, decision),
        )


_SURFACE_ANSWER_ENGINE = SurfaceAnswerEngine()


def build_context_frame_followup_context(frame: ContextFrame) -> str:
    """Compatibility wrapper for existing frame-follow-up callers."""
    return _SURFACE_ANSWER_ENGINE.build_context(frame)


def build_context_frame_followup_context_for_state(state: OrchestratorState) -> str:
    """Build follow-up interpreter context from current and related active frames."""
    return _SURFACE_ANSWER_ENGINE.build_state_context(state)


def build_context_frame_followup_response(
    state: OrchestratorState,
    text: str,
    *,
    decision: ContextFrameFollowupDecision | None = None,
    replay_modifier: ContextFrameReplayModifier | None = None,
) -> ContextFrameFollowupResponse | None:
    """Compatibility wrapper around SurfaceAnswerEngine."""
    return _SURFACE_ANSWER_ENGINE.answer(
        SurfaceAnswerRequest(
            state=state,
            text=text,
            decision=decision,
            replay_modifier=replay_modifier,
        )
    )


__all__ = [
    "ContextFrameFollowupResponse",
    "SurfaceAnswerEngine",
    "SurfaceAnswerRequest",
    "build_context_frame_followup_context",
    "build_context_frame_followup_context_for_state",
    "build_context_frame_followup_response",
]
