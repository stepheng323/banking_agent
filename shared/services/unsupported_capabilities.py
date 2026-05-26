"""Deterministic registry for unsupported capability boundaries."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

SUPPORTED_BANKING_ALTERNATIVES = "transfers, airtime/data, balances, and transaction queries"
UNSUPPORTED_CAPABILITY_SEMANTIC_CONFIDENCE = 0.84
UNSUPPORTED_BOUNDARY_TURN_CONFIDENCE = 0.78
SUPPORTED_BANKING_ALTERNATIVES_BY_LOCALE: Mapping[str, str] = {
    "en": SUPPORTED_BANKING_ALTERNATIVES,
    "pcm": "transfers, airtime/data, balances, and transaction queries",
    "yo": "transfer, airtime/data, balance, ati wiwa transaction",
    "ha": "transfer, airtime/data, balance, da binciken transaction",
    "ig": "transfer, airtime/data, balance, na nyocha transaction",
}
_PLANNER_ALTERNATIVE_LABELS_BY_LOCALE: Mapping[str, Mapping[str, str]] = {
    "pcm": {
        "send money": "send money",
        "review recent transactions": "review recent transactions",
        "check balances": "check balances",
    },
    "yo": {
        "send money": "transfer owo",
        "review recent transactions": "wiwa recent transactions",
        "check balances": "wiwo balance",
    },
    "ha": {
        "send money": "transfer kudi",
        "review recent transactions": "binciken recent transactions",
        "check balances": "duba balance",
    },
    "ig": {
        "send money": "transfer ego",
        "review recent transactions": "nyocha recent transactions",
        "check balances": "ilele balance",
    },
}


@dataclass(frozen=True, slots=True)
class UnsupportedCapability:
    key: str
    label: str
    policy_label: str
    patterns: tuple[re.Pattern[str], ...]
    followup_terms: tuple[str, ...]
    planner_alternatives: tuple[str, ...]
    supported_alternatives: str = SUPPORTED_BANKING_ALTERNATIVES
    safety_note: str | None = None
    labels_by_locale: Mapping[str, str] = field(default_factory=dict)
    supported_alternatives_by_locale: Mapping[str, str] = field(default_factory=dict)


def _compile(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns)


def _locale_key(locale: str | None) -> str:
    key = (locale or "en").strip().split("-")[0].casefold()
    return key if key in SUPPORTED_BANKING_ALTERNATIVES_BY_LOCALE else "en"


def localized_supported_alternatives(locale: str | None = None) -> str:
    return SUPPORTED_BANKING_ALTERNATIVES_BY_LOCALE[_locale_key(locale)]


UNSUPPORTED_CAPABILITY_REGISTRY: tuple[UnsupportedCapability, ...] = (
    UnsupportedCapability(
        key="financial_advice",
        label="financial advice",
        policy_label="Financial advice",
        patterns=_compile(
            r"\b(?:financial\s+)?advi[cs]e\b",
            r"\bwhat\s+(?:stock|stocks|share|shares|investment|crypto|coin|coins)\s+should\s+i\s+"
            r"(?:buy|sell|choose|pick|invest)\b",
            r"\bshould\s+i\s+(?:buy|sell|invest|save)\b",
            r"\brecommend(?:\s+(?:a|an|me))?\s+(?:stock|stocks|investment|crypto|coin|coins|fund)\b",
            r"\b(?:imoran|advice)\s+(?:owo|idoko|investment|crypto)\b",
            r"\b(?:ki|kin)\s+(?:n\s+)?(?:ra|ta|invest)\b.*\b(?:stock|share|crypto|bitcoin|coin|idoko)\b",
            r"\bshawarar?\s+(?:kudi|zuba\s+jari|investment|crypto)\b",
            r"\b(?:wane|wace|me)\s+(?:stock|share|crypto|bitcoin|coin)\s+zan\s+(?:saya|zaba|zuba)\b",
            r"\bndumodu\s+(?:ego|itinye\s+ego|investment|crypto)\b",
            r"\bgini\s+ka\s+m\s+(?:zuta|tinye)\b.*\b(?:stock|share|crypto|bitcoin|coin)\b",
        ),
        followup_terms=(
            "advice",
            "advise",
            "recommend",
            "should",
            "stock",
            "crypto",
            "invest",
            "imoran",
            "shawara",
            "ndumodu",
        ),
        planner_alternatives=("review recent transactions", "check balances"),
        safety_note="Do not give financial advice or investment recommendations.",
        labels_by_locale={
            "pcm": "financial advice",
            "yo": "imoran owo",
            "ha": "shawarar kudi",
            "ig": "ndumodu ego",
        },
    ),
    UnsupportedCapability(
        key="investments",
        label="investments or crypto",
        policy_label="Investments",
        patterns=_compile(
            r"\b(?:invest|investment|stocks?|shares?|mutual\s+funds?|crypto|bitcoin|btc|ethereum|eth|coins?)\b",
            r"\bbuy\s+(?:bitcoin|btc|crypto|ethereum|eth|stocks?|shares?|mutual\s+funds?)\b",
            r"\btrade\s+(?:crypto|bitcoin|btc|stocks?|shares?|forex|fx)\b",
            r"\b(?:buy|trade|invest)\s+(?:coin|coins|crypto|bitcoin|btc|ethereum|eth)\b",
            r"\b(?:ra|ta)\s+(?:bitcoin|btc|crypto|ethereum|eth|stock|shares?|coin|coins?)\b",
            r"\bidoko\s+owo\b",
            r"\b(?:sayi|sayar|zuba)\s+(?:bitcoin|btc|crypto|ethereum|eth|stock|shares?|coin|coins?)\b",
            r"\bzuba\s+jari\b",
            r"\bhannun\s+jari\b",
            r"\b(?:zuta|zuru|tinye)\s+(?:bitcoin|btc|crypto|ethereum|eth|stock|shares?|coin|coins?)\b",
            r"\bitinye\s+ego\b",
        ),
        followup_terms=(
            "invest",
            "investment",
            "stock",
            "share",
            "crypto",
            "bitcoin",
            "btc",
            "ethereum",
            "coin",
            "idoko",
            "jari",
            "zuba",
            "hannun",
            "tinye",
            "zuta",
        ),
        planner_alternatives=("send money", "review recent transactions"),
        safety_note="Do not buy, sell, trade, or recommend investments or crypto.",
        labels_by_locale={
            "pcm": "investments or crypto",
            "yo": "idoko owo tabi crypto",
            "ha": "zuba jari ko crypto",
            "ig": "itinye ego ma obu crypto",
        },
    ),
    UnsupportedCapability(
        key="lending",
        label="loans or lending",
        policy_label="Loans or lending",
        patterns=_compile(
            r"^(?:(?:please|pls|abeg|oya|jowo|biko|kindly)\s+)*"
            r"(?:(?:can|could|will|would)\s+you\s+(?:borrow|lend|loan|advance)\s+(?:me|us)\b|"
            r"(?:you\s+fit\s+)?(?:borrow|lend|loan|advance)\s+(?:me|us)\b|"
            r"(?:can|could)\s+i\s+(?:borrow|get|take)\s+(?:a\s+)?loan\b|"
            r"(?:give|offer|provide)\s+(?:me|us)\s+(?:a\s+)?loan\b|"
            r"i\s+(?:need|want|would\s+like)\s+(?:to\s+borrow|a\s+loan|loan)\b)",
            r"\b(?:loan|loans|lending|lend\s+me|borrow\s+me|credit\s+me|salary\s+advance)\b",
            r"\b(?:ya\s+mi\s+lowo|fun\s+mi\s+ni\s+loan|mo\s+fe\s+loan|awin|owo\s+awin|gbese)\b",
            r"\b(?:ba\s+ni|bani|a\s+ba\s+ni)\s+(?:rance|lamuni|bashi)\b",
            r"\b(?:ina\s+son|ina\s+bukatar)\s+(?:rance|lamuni|bashi)\b",
            r"\b(?:rance|lamuni|bashi)\b",
            r"\b(?:binye\s+m\s+ego|ego\s+mbinye|mbinye\s+ego|nye\s+m\s+loan|achoro\s+m\s+loan)\b",
        ),
        followup_terms=(
            "borrow",
            "lend",
            "loan",
            "loans",
            "lending",
            "advance",
            "pay back",
            "payback",
            "repay",
            "return",
            "owe",
            "credit",
            "awin",
            "gbese",
            "rance",
            "lamuni",
            "bashi",
            "mbinye",
            "binye",
        ),
        planner_alternatives=("send money", "check balances"),
        safety_note="Do not lend money, arrange loans, approve credit, or suggest lenders.",
        labels_by_locale={
            "pcm": "loans or lending",
            "yo": "awin tabi loan",
            "ha": "bashi ko lamuni",
            "ig": "ego mbinye ma obu loan",
        },
    ),
    UnsupportedCapability(
        key="international_transfers",
        label="international transfers",
        policy_label="International transfers",
        patterns=_compile(
            r"\binternational\s+transfers?\b",
            r"\bsend\s+(?:money\s+)?abroad\b",
            r"\b(?:swift|iban)\b",
            r"\b(?:dollar|usd|eur|gbp)\s+transfers?\b",
            r"\bsend\s+(?:dollars?|usd|pounds?|gbp|euros?|eur)\b",
            r"\bsend\s+(?:money|cash)?\s*(?:go\s+)?abroad\b",
            r"\b(?:fi|ran|firanse|send)\s+owo\s+(?:si|lo\s+si)\s+(?:ilu\s+okeere|abroad)\b",
            r"\b(?:aika|tura)\s+kudi\s+(?:zuwa\s+)?(?:waje|kasashen\s+waje|abroad)\b",
            r"\b(?:zipu|ziga|send)\s+ego\s+(?:na\s+)?(?:mba\s+ofesi|mba\s+ozo|abroad)\b",
        ),
        followup_terms=(
            "international",
            "abroad",
            "swift",
            "iban",
            "dollar",
            "usd",
            "eur",
            "gbp",
            "okeere",
            "waje",
            "ofesi",
        ),
        planner_alternatives=("send money",),
        safety_note="Do not start or promise international transfers.",
        labels_by_locale={
            "pcm": "international transfers",
            "yo": "fifiranse owo si ilu okeere",
            "ha": "aika kudi zuwa kasashen waje",
            "ig": "izipu ego mba ofesi",
        },
    ),
    UnsupportedCapability(
        key="csv_exports",
        label="CSV exports",
        policy_label="CSV exports",
        patterns=_compile(
            r"\bcsv\b",
            r"\bexport\s+(?:csv|spreadsheet)\b",
            r"\bdownload\s+(?:csv|spreadsheet)\b",
            r"\b(?:export|download|sauke|budata|fitar\s+da|gbe|fa)\b.*\b(?:csv|spreadsheet)\b",
        ),
        followup_terms=("csv", "spreadsheet", "export", "download"),
        planner_alternatives=("review recent transactions",),
        safety_note="Do not promise CSV export generation or downloads.",
        labels_by_locale={
            "pcm": "CSV exports",
            "yo": "CSV export",
            "ha": "CSV export",
            "ig": "CSV export",
        },
    ),
    UnsupportedCapability(
        key="pdf_exports",
        label="PDF exports",
        policy_label="PDF exports",
        patterns=_compile(
            r"\bpdf\b",
            r"\bexport\s+(?:my\s+)?(?:statement|transactions?|history|receipt)\b",
            r"\bdownload\s+(?:my\s+)?(?:statement|transactions?|history|receipt)\b",
            r"\b(?:export|download|sauke|budata|fitar\s+da|gbe|fa)\b.*\b(?:statement|transactions?|history|receipt)\b",
        ),
        followup_terms=("pdf", "export", "download", "statement"),
        planner_alternatives=("review recent transactions",),
        safety_note="Do not promise PDF export generation or downloads.",
        labels_by_locale={
            "pcm": "PDF exports",
            "yo": "PDF export",
            "ha": "PDF export",
            "ig": "PDF export",
        },
    ),
    UnsupportedCapability(
        key="all_time_history",
        label="all-time transaction history",
        policy_label="All-time transaction history",
        patterns=_compile(
            r"\ball[-\s]?time\b",
            r"\bentire\s+history\b",
            r"\blifetime\s+history\b",
            r"\ball\s+(?:my\s+)?transactions?\s+ever\b",
            r"\ball\s+(?:my\s+)?transaction\s+history\b",
            r"\bgbogbo\s+(?:my\s+)?transactions?\b",
            r"\bitan\s+(?:transaction\s+)?gbogbo\b",
            r"\b(?:duk|duka)\s+(?:my\s+)?transactions?\b",
            r"\btarihi\s+(?:transaction\s+)?(?:duka|gaba\s+daya)\b",
            r"\btransactions?\s+niile\b",
            r"\bakuko\s+(?:transaction\s+)?niile\b",
        ),
        followup_terms=("all-time", "all time", "entire", "lifetime", "ever", "gbogbo", "duka", "niile"),
        planner_alternatives=("review recent transactions",),
        safety_note="Do not promise all-time transaction history retrieval.",
        labels_by_locale={
            "pcm": "all-time transaction history",
            "yo": "itan transaction gbogbo",
            "ha": "tarihin transaction duka",
            "ig": "akuko transaction niile",
        },
    ),
)

_REGISTRY_BY_KEY = {capability.key: capability for capability in UNSUPPORTED_CAPABILITY_REGISTRY}
_REGISTRY_BY_POLICY_LABEL = {
    capability.policy_label.casefold(): capability for capability in UNSUPPORTED_CAPABILITY_REGISTRY
}
_ALLOWED_CAPABILITY_KEYS = frozenset(_REGISTRY_BY_KEY)
_SEMANTIC_UNSUPPORTED_CANDIDATE_RE = re.compile(
    r"\b(?:"
    r"loan|borrow|lend|credit|advance|invest|investment|crypto|bitcoin|btc|ethereum|eth|stock|shares?|"
    r"forex|fx|advice|advise|recommend|abroad|international|dollar|usd|swift|iban|export|download|"
    r"pdf|csv|spreadsheet|all[-\s]?time|lifetime|entire\s+history|grow\s+(?:my\s+)?money|"
    r"wealth|returns?|profit|staking?|stake|portfolio|"
    r"owo|kudi|ego|jari|bashi|lamuni|rance|awin|gbese|mbinye|okeere|waje|ofesi"
    r")\b",
    re.IGNORECASE,
)


UnsupportedCapabilitySemanticAction = Literal["unsupported", "mixed", "supported_or_other", "unclear"]
UnsupportedBoundaryTurnAction = Literal[
    "same_unsupported",
    "new_unsupported",
    "supported_banking",
    "unrelated",
    "unclear",
]


class UnsupportedCapabilitySemanticOutput(BaseModel):
    """Structured LLM output for unsupported capability classification."""

    action: UnsupportedCapabilitySemanticAction = Field(
        description=(
            "unsupported when the user asks only for an unsupported capability; mixed when the turn has both a "
            "supported banking request and an unsupported capability; supported_or_other for supported banking, "
            "casual, or unrelated turns; unclear when uncertain."
        )
    )
    capability_key: str | None = Field(
        default=None,
        description="One known unsupported capability key, or null if none is clearly present.",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(default="semantic_classification")


class UnsupportedBoundaryTurnOutput(BaseModel):
    """Structured LLM output for turns after an unsupported capability refusal."""

    action: UnsupportedBoundaryTurnAction = Field(
        description=(
            "same_unsupported when the user continues the active unsupported topic; new_unsupported when they ask for "
            "a different known unsupported capability; supported_banking for a fresh supported banking request; "
            "unrelated for casual or unrelated turns; unclear when uncertain."
        )
    )
    capability_key: str | None = Field(
        default=None,
        description="Known unsupported capability key for same_unsupported/new_unsupported, otherwise null.",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(default="boundary_turn_classification")


def normalize_unsupported_text(text: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    without_marks = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", without_marks.strip().casefold()).strip()


def detect_unsupported_capability(text: str | None) -> UnsupportedCapability | None:
    normalized = normalize_unsupported_text(text)
    if not normalized:
        return None
    for capability in UNSUPPORTED_CAPABILITY_REGISTRY:
        if any(pattern.search(normalized) for pattern in capability.patterns):
            return capability
    return None


def detect_unsupported_capabilities(
    text: str | None,
    *,
    allowed_policy_labels: Iterable[str] | None = None,
) -> list[UnsupportedCapability]:
    normalized = normalize_unsupported_text(text)
    if not normalized:
        return []
    allowed = {label.casefold() for label in allowed_policy_labels or () if label}
    detected: list[UnsupportedCapability] = []
    for capability in UNSUPPORTED_CAPABILITY_REGISTRY:
        if allowed and capability.policy_label.casefold() not in allowed:
            continue
        if any(pattern.search(normalized) for pattern in capability.patterns):
            detected.append(capability)
    return detected


def get_unsupported_capability(key: str | None) -> UnsupportedCapability | None:
    return _REGISTRY_BY_KEY.get((key or "").strip())


def get_unsupported_capability_by_policy_label(label: str | None) -> UnsupportedCapability | None:
    return _REGISTRY_BY_POLICY_LABEL.get((label or "").strip().casefold())


def should_try_semantic_unsupported_capability(text: str | None) -> bool:
    normalized = normalize_unsupported_text(text)
    if not normalized or len(normalized) > 240:
        return False
    return bool(_SEMANTIC_UNSUPPORTED_CANDIDATE_RE.search(normalized))


def validate_semantic_unsupported_capability(
    decision: UnsupportedCapabilitySemanticOutput | Mapping[str, object],
    *,
    min_confidence: float = UNSUPPORTED_CAPABILITY_SEMANTIC_CONFIDENCE,
    allow_mixed: bool = False,
) -> UnsupportedCapability | None:
    parsed = (
        decision
        if isinstance(decision, UnsupportedCapabilitySemanticOutput)
        else UnsupportedCapabilitySemanticOutput.model_validate(decision)
    )
    if parsed.action == "mixed" and not allow_mixed:
        return None
    if parsed.action not in {"unsupported", "mixed"}:
        return None
    if parsed.confidence < min_confidence:
        return None
    key = (parsed.capability_key or "").strip()
    if key not in _ALLOWED_CAPABILITY_KEYS:
        return None
    return get_unsupported_capability(key)


def validate_unsupported_boundary_turn(
    decision: UnsupportedBoundaryTurnOutput | Mapping[str, object],
    *,
    boundary_key: str,
    min_confidence: float = UNSUPPORTED_BOUNDARY_TURN_CONFIDENCE,
) -> UnsupportedBoundaryTurnOutput | None:
    parsed = (
        decision
        if isinstance(decision, UnsupportedBoundaryTurnOutput)
        else UnsupportedBoundaryTurnOutput.model_validate(decision)
    )
    if parsed.confidence < min_confidence:
        return None
    if parsed.action == "same_unsupported":
        key = (parsed.capability_key or boundary_key).strip()
        if key != boundary_key or key not in _ALLOWED_CAPABILITY_KEYS:
            return None
        return parsed.model_copy(update={"capability_key": boundary_key})
    if parsed.action == "new_unsupported":
        key = (parsed.capability_key or "").strip()
        if key not in _ALLOWED_CAPABILITY_KEYS:
            return None
        return parsed
    if parsed.action in {"supported_banking", "unrelated", "unclear"}:
        return parsed.model_copy(update={"capability_key": None})
    return None


def unsupported_capability_label(capability: UnsupportedCapability, locale: str | None = None) -> str:
    return capability.labels_by_locale.get(_locale_key(locale), capability.label)


def unsupported_capability_supported_alternatives(
    capability: UnsupportedCapability,
    locale: str | None = None,
) -> str:
    return capability.supported_alternatives_by_locale.get(
        _locale_key(locale),
        capability.supported_alternatives
        if locale is None
        else localized_supported_alternatives(locale),
    )


def unsupported_capability_params(
    capability: UnsupportedCapability | Mapping[str, object],
    *,
    locale: str | None = None,
) -> dict[str, object]:
    if isinstance(capability, UnsupportedCapability):
        return {
            "capability_key": capability.key,
            "capability": unsupported_capability_label(capability, locale),
            "supported": unsupported_capability_supported_alternatives(capability, locale),
        }
    key = str(capability.get("key") or capability.get("capability_key") or "")
    registered = get_unsupported_capability(key)
    if registered is not None:
        return unsupported_capability_params(registered, locale=locale)
    return {
        "capability_key": key,
        "capability": str(capability.get("label") or capability.get("capability") or "that capability"),
        "supported": str(capability.get("supported") or localized_supported_alternatives(locale)),
    }


def format_planner_alternatives(
    capability_labels: Iterable[str],
    *,
    locale: str | None = None,
) -> list[str]:
    alternatives: list[str] = []
    localized = _PLANNER_ALTERNATIVE_LABELS_BY_LOCALE.get(_locale_key(locale), {})
    for label in capability_labels:
        capability = get_unsupported_capability_by_policy_label(label)
        if not capability:
            continue
        for alternative in capability.planner_alternatives:
            localized_alternative = localized.get(alternative, alternative)
            if localized_alternative and localized_alternative not in alternatives:
                alternatives.append(localized_alternative)
            if len(alternatives) >= 2:
                return alternatives
    return alternatives


def unsupported_capability_semantic_messages(
    *,
    text: str,
    locale: str | None = None,
    context: str = "None",
) -> list[dict[str, str]]:
    registry_lines = "\n".join(
        f"- {capability.key}: {capability.policy_label}; examples: {', '.join(capability.followup_terms[:5])}"
        for capability in UNSUPPORTED_CAPABILITY_REGISTRY
    )
    system_prompt = (
        "Classify whether the user asks for an unsupported capability for a Nigerian banking assistant.\n"
        "Return only one of the known registry keys. Do not invent categories.\n"
        "Known unsupported capabilities:\n"
        f"{registry_lines}\n\n"
        "Supported banking capabilities are local transfers, airtime/data purchase, balances, beneficiaries, "
        "scheduled transaction management, receipts/support for existing transactions, and transaction queries.\n"
        "Rules:\n"
        "- action=unsupported only when the whole turn is asking for one unsupported capability.\n"
        "- action=mixed only when the same turn clearly contains a supported banking request and an unsupported "
        "capability.\n"
        "- action=supported_or_other for supported banking, harmless chat, greetings, identity questions, or anything "
        "outside these unsupported categories.\n"
        "- action=unclear when the category is not clear.\n"
        "- Choose financial_advice for recommendations or 'what should I buy/sell' questions.\n"
        "- Choose investments for requests to buy, sell, trade, stake, or hold crypto, stocks, forex, or investments.\n"
        "- Choose lending for requests to borrow, get credit, obtain loans, or salary advances.\n"
        "- Choose international_transfers only for sending money across countries or foreign-currency transfer rails.\n"
        "- Choose pdf_exports/csv_exports for statement/history/receipt export or download requests.\n"
        "- Choose all_time_history only for all-time, lifetime, entire, or all-ever transaction history requests.\n"
        "- Be semantic across English, Nigerian Pidgin, Yoruba, Hausa, Igbo, and mixed language."
    )
    user_prompt = (
        f"Locale hint: {locale or 'unknown'}\n"
        f"Context: {context or 'None'}\n"
        f"User message: \"\"\"{text}\"\"\""
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


async def classify_unsupported_capability_semantic(
    text: str,
    *,
    locale: str | None = None,
    context: str = "None",
    structured_llm: Any | None = None,
) -> UnsupportedCapabilitySemanticOutput:
    if structured_llm is None:
        return UnsupportedCapabilitySemanticOutput(
            action="unclear",
            capability_key=None,
            confidence=0.0,
            reason="semantic_classifier_unavailable",
        )
    try:
        output = await structured_llm.ainvoke(
            unsupported_capability_semantic_messages(text=text, locale=locale, context=context)
        )
        return (
            output
            if isinstance(output, UnsupportedCapabilitySemanticOutput)
            else UnsupportedCapabilitySemanticOutput.model_validate(output)
        )
    except Exception:
        return UnsupportedCapabilitySemanticOutput(
            action="unclear",
            capability_key=None,
            confidence=0.0,
            reason="semantic_classifier_failed",
        )


def unsupported_boundary_turn_messages(
    *,
    text: str,
    boundary_key: str,
    boundary_label: str,
    followup_count: int,
    locale: str | None = None,
    context: str = "None",
) -> list[dict[str, str]]:
    registry_lines = "\n".join(
        f"- {capability.key}: {capability.policy_label}" for capability in UNSUPPORTED_CAPABILITY_REGISTRY
    )
    system_prompt = (
        "Classify the user's next turn after a Nigerian banking assistant refused an unsupported capability.\n"
        "This is routing only; do not write the reply.\n"
        f"Active unsupported boundary: {boundary_key} ({boundary_label}).\n"
        "Known unsupported capabilities:\n"
        f"{registry_lines}\n\n"
        "Supported banking capabilities are local transfers, airtime/data purchase, balances, beneficiaries, "
        "scheduled transaction management, receipts/support for existing transactions, and transaction queries.\n"
        "Actions:\n"
        "- same_unsupported: user continues, pleads, negotiates, asks for an exception, offers repayment/benefit, "
        "or otherwise stays on the active unsupported topic even without naming it.\n"
        "- new_unsupported: user asks for a different known unsupported capability.\n"
        "- supported_banking: user makes a fresh supported banking request.\n"
        "- unrelated: user moves to harmless casual chat, identity, gratitude, or unrelated content.\n"
        "- unclear: you cannot tell.\n"
        "Never classify a fresh supported banking request as same_unsupported. Be semantic across English, Nigerian "
        "Pidgin, Yoruba, Hausa, Igbo, and mixed language."
    )
    user_prompt = (
        f"Locale hint: {locale or 'unknown'}\n"
        f"Follow-up count so far: {followup_count}\n"
        f"Context: {context or 'None'}\n"
        f"User message: \"\"\"{text}\"\"\""
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


async def classify_unsupported_boundary_turn_semantic(
    text: str,
    *,
    boundary_key: str,
    boundary_label: str,
    followup_count: int = 0,
    locale: str | None = None,
    context: str = "None",
    structured_llm: Any | None = None,
) -> UnsupportedBoundaryTurnOutput:
    if structured_llm is None:
        return UnsupportedBoundaryTurnOutput(
            action="unclear",
            capability_key=None,
            confidence=0.0,
            reason="boundary_turn_classifier_unavailable",
        )
    try:
        output = await structured_llm.ainvoke(
            unsupported_boundary_turn_messages(
                text=text,
                boundary_key=boundary_key,
                boundary_label=boundary_label,
                followup_count=followup_count,
                locale=locale,
                context=context,
            )
        )
        return (
            output
            if isinstance(output, UnsupportedBoundaryTurnOutput)
            else UnsupportedBoundaryTurnOutput.model_validate(output)
        )
    except Exception:
        return UnsupportedBoundaryTurnOutput(
            action="unclear",
            capability_key=None,
            confidence=0.0,
            reason="boundary_turn_classifier_failed",
        )


def is_same_unsupported_capability_followup(text: str | None, capability: UnsupportedCapability) -> bool:
    normalized = normalize_unsupported_text(text)
    if not normalized:
        return False
    if any(pattern.search(normalized) for pattern in capability.patterns):
        return True
    return any(re.search(rf"\b{re.escape(term)}\b", normalized) for term in capability.followup_terms)
