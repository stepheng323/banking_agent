"""Registry and lookup helpers for unsupported capabilities."""

import re

from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_models import (
    SUPPORTED_BANKING_ALTERNATIVES_BY_LOCALE,
    UnsupportedCapability,
)

PLANNER_ALTERNATIVE_LABELS_BY_LOCALE = {
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


def _compile(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns)


def locale_key(locale: str | None) -> str:
    key = (locale or "en").strip().split("-")[0].casefold()
    return key if key in SUPPORTED_BANKING_ALTERNATIVES_BY_LOCALE else "en"


def localized_supported_alternatives(locale: str | None = None) -> str:
    return SUPPORTED_BANKING_ALTERNATIVES_BY_LOCALE[locale_key(locale)]


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
            r"\b(?:invest|investment|stocks?|stock\s+shares?|company\s+shares?|mutual\s+funds?|crypto|bitcoin|btc|ethereum|eth|coins?)\b",
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
            r"\b(?:need\s+money|want\s+money|give\s+me\s+money|borrow\s+me\s+money|lend\s+me\s+money)\b",
            r"\b(?:mo\s+fe\s+owo|nilo\s+owo|fun\s+mi\s+lọwọ|fun\s+mi\s+n[io]\s+owo)\b",
            r"\b(?:nye\s+m\s+ego|choro\s+ego|nilo\s+ego)\b",
            r"\b(?:ba\s+ni\s+kudi|ina\s+son\s+kudi|ina\s+bukatar\s+kudi)\b",
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
        planner_alternatives=("check balances", "review recent transactions"),
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

REGISTRY_BY_KEY = {capability.key: capability for capability in UNSUPPORTED_CAPABILITY_REGISTRY}
REGISTRY_BY_POLICY_LABEL = {
    capability.policy_label.casefold(): capability for capability in UNSUPPORTED_CAPABILITY_REGISTRY
}
ALLOWED_CAPABILITY_KEYS = frozenset(REGISTRY_BY_KEY)


def get_unsupported_capability(key: str | None) -> UnsupportedCapability | None:
    return REGISTRY_BY_KEY.get((key or "").strip())


def get_unsupported_capability_by_policy_label(label: str | None) -> UnsupportedCapability | None:
    return REGISTRY_BY_POLICY_LABEL.get((label or "").strip().casefold())
