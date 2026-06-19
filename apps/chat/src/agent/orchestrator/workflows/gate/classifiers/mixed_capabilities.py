import re
from dataclasses import dataclass
from typing import Any, Literal

from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_detection import (
    detect_unsupported_capability,
    should_try_semantic_unsupported_capability,
)
from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_models import UnsupportedCapability
from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_presentation import (
    unsupported_capability_label,
)
from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_semantic import (
    validate_semantic_unsupported_capability,
)
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.direct_domains import (
    _is_account_balance_request,
    _is_account_domain_request,
    _is_beneficiary_domain_request,
    _is_query_domain_request,
    _is_structural_query_domain_request,
)
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.transaction_intents import (
    _classify_obvious_transfer_request,
    _is_obvious_airtime_request,
    _is_obvious_data_request,
)
from banking.presentation.i18n.renderer import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)

SupportedDomain = Literal["transfer", "airtime", "data", "account", "beneficiary", "query", "schedule"]

_CLAUSE_SPLIT_RE = re.compile(
    r"(?:\s+(?:and|then|also|plus)\s+|[;\n]+|(?<!\d),(?!\d))",
    re.IGNORECASE,
)
_SCHEDULE_READ_RE = re.compile(r"\b(?:show|list|view|get|check)\b.*\b(?:scheduled|recurring|schedule)\w*\b", re.I)
_SUPPORTED_LABELS: dict[SupportedDomain, str] = {
    "transfer": "money transfer",
    "airtime": "airtime purchase",
    "data": "data purchase",
    "account": "balance or account action",
    "beneficiary": "beneficiary management",
    "query": "transaction query",
    "schedule": "scheduled transaction management",
}
_SUPPORTED_LABELS_BY_LOCALE: dict[str, dict[SupportedDomain, str]] = {
    "pcm": {
        "transfer": "money transfer",
        "airtime": "airtime purchase",
        "data": "data purchase",
        "account": "balance or account action",
        "beneficiary": "beneficiary management",
        "query": "transaction query",
        "schedule": "scheduled transaction management",
    },
    "yo": {
        "transfer": "transfer owo",
        "airtime": "rira airtime",
        "data": "rira data",
        "account": "balance tabi account",
        "beneficiary": "beneficiary management",
        "query": "wiwa transaction",
        "schedule": "scheduled transaction management",
    },
    "ha": {
        "transfer": "transfer kudi",
        "airtime": "sayan airtime",
        "data": "sayan data",
        "account": "balance ko account",
        "beneficiary": "beneficiary management",
        "query": "binciken transaction",
        "schedule": "scheduled transaction management",
    },
    "ig": {
        "transfer": "transfer ego",
        "airtime": "izuta airtime",
        "data": "izuta data",
        "account": "balance ma obu account",
        "beneficiary": "beneficiary management",
        "query": "nyocha transaction",
        "schedule": "scheduled transaction management",
    },
}


@dataclass(frozen=True, slots=True)
class SupportedClause:
    domain: SupportedDomain
    text: str
    heuristic_name: str


@dataclass(frozen=True, slots=True)
class MixedCapabilityMatch:
    supported: tuple[SupportedClause, ...]
    unsupported: tuple[UnsupportedCapability, ...]

    @property
    def is_ambiguous(self) -> bool:
        return len(self.supported) != 1


def _join_labels(labels: list[str]) -> str:
    unique = [label for index, label in enumerate(labels) if label and label not in labels[:index]]
    if not unique:
        return ""
    if len(unique) == 1:
        return unique[0]
    if len(unique) == 2:
        return f"{unique[0]} and {unique[1]}"
    return f"{', '.join(unique[:-1])}, and {unique[-1]}"


def _locale_key(locale: str | None) -> str:
    key = (locale or "en").strip().split("-")[0].casefold()
    return key if key in _SUPPORTED_LABELS_BY_LOCALE else "en"


def _supported_label(domain: SupportedDomain, locale: str | None) -> str:
    locale_labels = _SUPPORTED_LABELS_BY_LOCALE.get(_locale_key(locale), {})
    return locale_labels.get(domain, _SUPPORTED_LABELS[domain])


def _split_clauses(text: str | None) -> list[str]:
    return [clause.strip(" \t\r\n.,;:") for clause in _CLAUSE_SPLIT_RE.split(text or "") if clause.strip()]


def _classify_supported_clause(text: str) -> SupportedClause | None:
    normalized = re.sub(r"\s+", " ", text.strip().lower()).rstrip("?.!,")
    if not normalized:
        return None

    if _is_account_balance_request(normalized):
        return SupportedClause("account", text, "balance_request")
    if _is_beneficiary_domain_request(normalized):
        return SupportedClause("beneficiary", text, "beneficiary_list_request")
    if _is_obvious_airtime_request(normalized):
        return SupportedClause("airtime", text, "obvious_airtime_request")
    if _is_obvious_data_request(normalized):
        return SupportedClause("data", text, "obvious_data_request")
    transfer_reason = _classify_obvious_transfer_request(text)
    if transfer_reason in {
        "fresh_transfer_command",
        "fresh_transfer_missing_recipient_command",
        "recipient_bank_details_only",
    }:
        return SupportedClause("transfer", text, transfer_reason)
    if _is_account_domain_request(normalized):
        return SupportedClause("account", text, "account_domain_request")
    if _is_structural_query_domain_request(normalized) or _is_query_domain_request(normalized):
        return SupportedClause("query", text, "query_domain_request")
    if _SCHEDULE_READ_RE.search(normalized):
        return SupportedClause("schedule", text, "schedule_read_request")
    return None


def analyze_mixed_supported_unsupported(text: str | None) -> MixedCapabilityMatch | None:
    supported: list[SupportedClause] = []
    unsupported: list[UnsupportedCapability] = []
    for clause in _split_clauses(text):
        unsupported_capability = detect_unsupported_capability(clause)
        if unsupported_capability is not None:
            if unsupported_capability.key not in {capability.key for capability in unsupported}:
                unsupported.append(unsupported_capability)
            continue
        supported_clause = _classify_supported_clause(clause)
        if supported_clause is not None:
            supported.append(supported_clause)

    if not unsupported or not supported:
        return None
    return MixedCapabilityMatch(supported=tuple(supported), unsupported=tuple(unsupported))


async def _semantic_unsupported_clause(
    *,
    capability_classifier_llm: Any,
    clause: str,
    locale: str,
) -> UnsupportedCapability | None:
    if capability_classifier_llm is None or not should_try_semantic_unsupported_capability(clause):
        return None
    try:
        decision = await capability_classifier_llm.classify_unsupported_capability(
            clause,
            locale=locale,
            context="None",
            path_label="direct_path",
        )
    except Exception:
        logger.warning("mixed_capability_semantic_clause_failed")
        return None
    return validate_semantic_unsupported_capability(decision)


async def analyze_mixed_supported_unsupported_semantic(
    *,
    text: str,
    locale: str,
    capability_classifier_llm: Any,
) -> MixedCapabilityMatch | None:
    clauses = _split_clauses(text)
    if len(clauses) < 2:
        return None

    supported: list[SupportedClause] = []
    unsupported: list[UnsupportedCapability] = []
    for clause in clauses:
        unsupported_capability = detect_unsupported_capability(clause)
        if unsupported_capability is not None:
            if unsupported_capability.key not in {capability.key for capability in unsupported}:
                unsupported.append(unsupported_capability)
            continue

        supported_clause = _classify_supported_clause(clause)
        if supported_clause is not None:
            supported.append(supported_clause)
            continue

        semantic_capability = await _semantic_unsupported_clause(
            capability_classifier_llm=capability_classifier_llm,
            clause=clause,
            locale=locale,
        )
        if semantic_capability is not None and semantic_capability.key not in {
            capability.key for capability in unsupported
        }:
            unsupported.append(semantic_capability)

    if not unsupported or not supported:
        return None
    return MixedCapabilityMatch(supported=tuple(supported), unsupported=tuple(unsupported))


def mixed_policy_notice(match: MixedCapabilityMatch, *, locale: str) -> str:
    supported_text = _join_labels([_supported_label(item.domain, locale) for item in match.supported])
    unsupported_text = _join_labels([unsupported_capability_label(item, locale) for item in match.unsupported])
    return render_message(
        "planner.mixed_supported_unsupported_notice",
        locale,
        {"supported": supported_text, "unsupported": unsupported_text},
    )


def mixed_clarify_params(match: MixedCapabilityMatch, *, locale: str) -> dict[str, str]:
    return {
        "supported": _join_labels([_supported_label(item.domain, locale) for item in match.supported]),
        "unsupported": _join_labels([unsupported_capability_label(item, locale) for item in match.unsupported]),
    }
