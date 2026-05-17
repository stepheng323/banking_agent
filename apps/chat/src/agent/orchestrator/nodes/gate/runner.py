"""Session Gate Node.

Applies deterministic guardrails, invokes the semantic router, and only falls through
to planner for planner-owned routes.
"""

import re
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.graphs.query.services.parser import QueryParser
from apps.chat.src.agent.graphs.query.services.query_shortcuts import resolve_query_shortcut_with_reason
from apps.chat.src.agent.graphs.query.utils.timezone import lagos_today
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.nodes.planner.context import (
    TurnContextSummary,
    build_router_context_from_summary,
)
from shared.config.settings import settings
from shared.i18n import LocaleManager
from shared.policy.service import capability_block_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)

TRANSACTION_EXECUTORS = {"transfer", "airtime", "data"}
DIRECT_DOMAIN_ACTIONS = {
    "transfer": "send_money",
    "airtime": "buy_airtime",
    "data": "buy_data",
    "support": "collect_details",
    "faq": "answer_question",
}
SEMANTIC_ROUTER_MULTI_CLAUSE_MARKERS = (" and ", " & ", " then ", ",")
_TRANSFER_DIRECT_PREFIX_RE = re.compile(
    r"^(?:(?:ok(?:ay)?|please|pls|abeg|oya|jowo|biko|kindly)\s+)*"
    r"(?:send|transfer|pay|remit|split|fi|tura)\b",
    re.IGNORECASE,
)
_TRANSFER_DIRECT_RECIPIENT_CUE_RE = re.compile(r"\b(?:to|for|between|btw|si|zuwa)\b", re.IGNORECASE)
_TRANSFER_DIRECT_AMOUNT_RE = re.compile(r"(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKmMhH]?\b")
_NIGERIAN_PHONE_RE_FRAGMENT = r"(?<!\d)(?:\+?234[\s().-]*[789]|0[789])(?:[\s().-]*\d){9}(?!\d)"
_PHONE_NUMBER_CUE_RE = re.compile(_NIGERIAN_PHONE_RE_FRAGMENT, re.IGNORECASE)
_TRANSFER_DIRECT_PERCENTAGE_RE = re.compile(
    r"\b(?:half|quarter|tithe|\d{1,3}\s*%|all|everything|max amount|what(?:ever)? i have)\b",
    re.IGNORECASE,
)
_TRANSFER_DIRECT_SOURCE_RE = re.compile(
    r"\b(?:from|using|use|with)\s+(?:my\s+)?[a-z][\w\s]{0,24}\b",
    re.IGNORECASE,
)
_TRANSFER_DIRECT_NON_TRANSFER_RE = re.compile(
    r"\b(?:transaction|transactions|history|statement|income|inflow|expense|expenses|spending|"
    r"balance|linked accounts?|beneficiar(?:y|ies)|save beneficiary|support|reversal|receipt|"
    r"airtime|data|bundle|show|list|view|get)\b",
    re.IGNORECASE,
)
_TRANSFER_DIRECT_QUERY_MARKER_RE = re.compile(
    r"\b(?:how much|what(?:'s| is)?|when|who did i|show|list|view|get)\b",
    re.IGNORECASE,
)
_TRANSFER_MULTI_RECIPIENT_TAIL_RE = re.compile(
    r"\b(?:to|for|between|btw)\b\s+.+\b(?:and|&)\b\s+.+",
    re.IGNORECASE,
)
DETERMINISTIC_GREETING_EXACT = {
    "hi",
    "hello",
    "hey",
    "how far",
    "sup",
    "what's up",
    "what s up",
    "good morning",
    "good afternoon",
    "good evening",
}
DETERMINISTIC_APPRECIATION_EXACT = {
    "thanks",
    "thank you",
    "thankyou",
}
DETERMINISTIC_CHECKIN_EXACT = {
    "are you there",
    "are you online",
    "how are you",
    "how is it going",
    "how's it going",
    "how s it going",
    "you online",
    "you there",
}
DETERMINISTIC_IDENTITY_EXACT = {
    "who are you",
    "what is your name",
    "what s your name",
    "what's your name",
}
_APP_NAME_LOWER = settings.app_name.lower()
_APP_NAME_SHORT_LOWER = settings.app_name_short.lower()
DETERMINISTIC_BRAND_ORIGIN_EXACT = {
    "who created you",
    "who built you",
    "who made you",
    f"what does {_APP_NAME_LOWER} mean",
    f"what is {_APP_NAME_LOWER}",
    f"what does {_APP_NAME_SHORT_LOWER} mean",
    f"what is {_APP_NAME_SHORT_LOWER}",
}
DETERMINISTIC_CAPABILITY_EXACT = {
    "what can you do",
    "what do you do",
    "what can you help me with",
    "what do you handle",
}
DETERMINISTIC_CAPABILITY_PATTERNS = (
    re.compile(
        r"^(?:can|could|will|would)\s+you\s+(?:help|assist)(?:\s+me)?\s+"
        r"(?:send|transfer|pay|buy|recharge|top\s*up|check|show|view|list)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:can|could|will|would)\s+you\s+(?:help|assist)(?:\s+me)?\s+(?:with\s+)?"
        r"(?:funds?|money|transfers?|payments?|airtime|data|balances?|transactions?)\b",
        re.IGNORECASE,
    ),
)
_SUPPORTED_SWITCHABLE_LOCALES = {"en", "pcm", "yo", "ha", "ig"}
_ENGLISH_FASTPATH_CUE_RE = re.compile(
    r"^(?:show|list|view|get|check|what(?:'s| is)|how much|send|transfer|pay|buy|recharge|top\s*up|topup|"
    r"link|unlink|set|make)\b",
    re.IGNORECASE,
)
_LANGUAGE_SWITCH_EXACT: dict[str, str] = {
    "switch to english": "en",
    "speak english": "en",
    "reply in english": "en",
    "continue in english": "en",
    "use english": "en",
    "switch to pidgin": "pcm",
    "switch to naija": "pcm",
    "speak pidgin": "pcm",
    "reply in pidgin": "pcm",
    "continue in pidgin": "pcm",
    "use pidgin": "pcm",
    "abeg yarn for pidgin": "pcm",
    "make we yarn pidgin": "pcm",
    "switch to yoruba": "yo",
    "speak yoruba": "yo",
    "reply in yoruba": "yo",
    "continue in yoruba": "yo",
    "use yoruba": "yo",
    "so yoruba": "yo",
    "so ede yoruba": "yo",
    "ba mi soro ni ede yoruba": "yo",
    "switch to hausa": "ha",
    "speak hausa": "ha",
    "reply in hausa": "ha",
    "continue in hausa": "ha",
    "use hausa": "ha",
    "yi magana da hausa": "ha",
    "switch to igbo": "ig",
    "speak igbo": "ig",
    "reply in igbo": "ig",
    "continue in igbo": "ig",
    "use igbo": "ig",
    "kwuo igbo": "ig",
}
DETERMINISTIC_LOCALE_META_EXACT: dict[str, tuple[str, str]] = {
    # Pidgin
    "wetin you fit do": ("conversational.capability_question", "pcm"),
    "who you be": ("conversational.identity", "pcm"),
    "who build you": ("conversational.brand_origin", "pcm"),
    "abeg": ("conversational.checkin", "pcm"),
    "how body": ("conversational.checkin", "pcm"),
    "how you dey": ("conversational.checkin", "pcm"),
    "you dey": ("conversational.checkin", "pcm"),
    # Yoruba
    "pele o": ("conversational.greeting", "yo"),
    "e se": ("conversational.appreciation", "yo"),
    "ese": ("conversational.appreciation", "yo"),
    "ta lo je": ("conversational.identity", "yo"),
    "kini o le se": ("conversational.capability_question", "yo"),
    "kini o ma n se": ("conversational.capability_question", "yo"),
    # Hausa
    "sannu": ("conversational.greeting", "ha"),
    "nagode": ("conversational.appreciation", "ha"),
    "kai wa ne": ("conversational.identity", "ha"),
    "me zaka iya yi": ("conversational.capability_question", "ha"),
    # Igbo
    "ndewo": ("conversational.greeting", "ig"),
    "dalu": ("conversational.appreciation", "ig"),
    "onye ka i bu": ("conversational.identity", "ig"),
    "gini ka i nwere ike ime": ("conversational.capability_question", "ig"),
}
DETERMINISTIC_LOCALE_META_PATTERNS: tuple[tuple[re.Pattern[str], tuple[str, str]], ...] = (
    (
        re.compile(
            r"^(?:(?:my\s+)?(?:g|guy|gee|bro|boss|oga|chairman|fam)[\s,]+)?"
            r"how\s+far"
            r"(?:\s+(?:(?:my\s+)?(?:g|guy|gee|bro|boss|oga|chairman|fam)|now|na))?$",
            re.IGNORECASE,
        ),
        ("conversational.greeting", "pcm"),
    ),
    (
        re.compile(
            r"^(?:(?:my\s+)?(?:g|guy|gee|bro|boss|oga|chairman|fam)[\s,]+)?"
            r"(?:how\s+you\s+dey|how\s+body|you\s+dey)"
            r"(?:\s+(?:now|na))?$",
            re.IGNORECASE,
        ),
        ("conversational.checkin", "pcm"),
    ),
)
ACCOUNT_BALANCE_REQUEST_PATTERNS = (
    r"\bbalance\b",
    r"\baccount\s+balance\b",
    r"\bcheck\s+my\s+balance\b",
    r"\bwhat(?:'s| is)\s+my\s+balance\b",
    r"\bhow\s+much\s+do\s+i\s+have\b",
    r"\bhow\s+much\s+is\s+in\s+my\s+account\b",
)
ACCOUNT_DOMAIN_PATTERNS = (
    r"^(?:(?:show|list|view|get|display|tell me)\s+)?(?:all\s+)?(?:my\s+)?(?:linked\s+)?accounts?\b",
    r"^what\s+(?:linked\s+)?accounts?\s+do\s+i\s+have\b",
    r"^how\s+many\s+accounts?\s+do\s+i\s+have\b",
    r"^do\s+i\s+have\s+any\s+(?:linked\s+)?accounts?\b",
    r"^(?:link|add)\s+(?:a\s+)?(?:new\s+|another\s+)?account\b",
    r"^(?:unlink|remove|disconnect)\s+(?:my\s+)?account\b",
    r"^(?:set|make)\s+.+\s+default\b",
)
BENEFICIARY_DOMAIN_PATTERNS = (
    r"^(?:(?:show|list|view|get)\s+)?(?:my\s+)?beneficiar(?:y|ies)\b",
    r"^who\s+do\s+i\s+have\s+saved\b",
    r"^(?:show|list|view|get)\s+(?:my\s+)?saved\s+(?:recipients?|beneficiar(?:y|ies))\b",
)
_AIRTIME_DIRECT_PREFIX_RE = re.compile(
    r"^(?:(?:ok(?:ay)?|please|pls|abeg|oya|jowo|biko|kindly)\s+)*"
    r"(?:buy|recharge|top\s*up|topup|load|send)\b",
    re.IGNORECASE,
)
_AIRTIME_DIRECT_HINT_RE = re.compile(
    rf"\b(?:airtime|mtn|glo|airtel|9mobile)\b|{_NIGERIAN_PHONE_RE_FRAGMENT}",
    re.IGNORECASE,
)
_AIRTIME_DIRECT_EXPLICIT_HINT_RE = re.compile(
    r"\b(?:airtime|mtn|glo|airtel|9mobile)\b",
    re.IGNORECASE,
)
_AIRTIME_DIRECT_SEND_PREFIX_RE = re.compile(
    r"^(?:(?:ok(?:ay)?|please|pls|abeg|oya|jowo|biko|kindly)\s+)*send\b",
    re.IGNORECASE,
)
_DATA_DIRECT_PREFIX_RE = re.compile(
    r"^(?:(?:ok(?:ay)?|please|pls|abeg|oya|jowo|biko|kindly)\s+)*"
    r"(?:buy|get|send)\b",
    re.IGNORECASE,
)
_DATA_DIRECT_HINT_RE = re.compile(
    r"\b(?:data|bundle)\b|\d+\s*(?:mb|gb)\b",
    re.IGNORECASE,
)
_DIRECT_CONTEXT_RECAP_EXACT = {
    "where did we stop",
    "what are we doing again",
    "what do you need again",
    "repeat that",
    "show it again",
}
BALANCE_DIRECT_TRANSACTION_HINT_PATTERNS = (
    r"\b(send|transfer|pay|buy|airtime|data|bundle|fund|withdraw)\b",
)
_MIXED_TRANSFER_CLAUSE_RE = re.compile(
    r"\b(?:send|transfer|pay|remit|split)\b.*(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKmMhH]?",
    re.IGNORECASE,
)
_MIXED_AIRTIME_CLAUSE_RE = re.compile(
    r"\b(?:buy|recharge|top\s*up|topup|load)\b.*\b(?:airtime|mtn|glo|airtel|9mobile)\b",
    re.IGNORECASE,
)
_MIXED_DATA_CLAUSE_RE = re.compile(
    r"\b(?:buy|get|send)\b.*\b(?:data|bundle|\d+\s*(?:mb|gb))\b",
    re.IGNORECASE,
)
BALANCE_DIRECT_CANCEL_PREFIX_RE = re.compile(
    r"^(?:cancel|abort|stop|nevermind|never\s+mind)(?:\s+(?:and|then))?\s+",
    re.IGNORECASE,
)
_QUERY_DOMAIN_PATTERNS = (
    r"^(?:(?:show|list|view|get)\s+)?(?:my\s+)?(?:transactions?|transaction\s+history|history|statement)",
    r"^(?:(?:show|list|view|get)\s+)?(?:my\s+)?last\s+\d+\s+transactions?",
    r"^how\s+much\s+(?:(?:total|in\s+total)\s+)?(?:did|have)\s+i\s+(?:spend|spent|send|sent|pay|paid|receive|received)",
    r"^(?:what(?:'s| is|'s)|how\s+much\s+is)\s+my\s+(?:spending|expenses?|income|inflow)",
    r"^who\s+did\s+i\s+(?:send|transfer|pay)\s+(?:money\s+)?to",
    r"^(?:top|my)\s+(?:recipients?|beneficiar)",
)
_STRUCTURAL_QUERY_DIRECT_PATTERNS = (
    r"^(?:(?:show|list|view|get)\s+)?(?:my\s+)?(?:transactions?|transaction\s+history|history|statement)\b",
    r"^(?:(?:show|list|view|get)\s+)?(?:my\s+)?last\s+\d+\s+transactions?\b",
)
_RECEIPT_REQUEST_RE = re.compile(
    r"\b(?:receipt|proof\s+of\s+payment|payment\s+receipt|show\s+receipt|send\s+receipt)\b",
    re.IGNORECASE,
)
_RECEIPT_SELECTOR_FOLLOWUP_RE = re.compile(
    r"\b(?:both|all|every|except|excluding|only|just|other(?:\s+one)?|remaining|rest|"
    r"first|second|third|fourth|fifth|last)\b|"
    r"(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKhH]?\b|"
    r"\bone\s+for\b",
    re.IGNORECASE,
)
EXPLICIT_CANCEL_PATTERNS = (
    r"\bcancel\b",
    r"\babort\b",
    r"\bstop\b",
    r"\bnevermind\b",
    r"\bnever\s+mind\b",
)
_BENEFICIARY_SUGGESTION_ALIAS_MAX_CHARS = 64
_BENEFICIARY_ALIAS_MARKERS = (" as ", " alias ", " name ", " called ", " oruko ", " suna ", " aha ", " nom ")
_BENEFICIARY_BARE_ALIAS_MAX_WORDS = 3
_BENEFICIARY_SAVE_AFFIRMATIONS = {
    "yes",
    "yes please",
    "ok",
    "okay",
    "sure",
    "proceed",
    "go ahead",
    "confirm",
    "save",
    "save it",
    "save am",
    "save this",
    "save beneficiary",
    "yes na",
    "ok na",
    "biko",
    "na'am",
    "naam",
    "ee",
    "beeni",
    "oui",
    "d'accord",
    "daccord",
}
_BENEFICIARY_DISMISS_PHRASES = {
    "no",
    "no thanks",
    "not now",
    "later",
    "skip",
    "dont save",
    "don't save",
    "leave it",
    "ignore",
    "cancel",
}
_BENEFICIARY_SAVE_INTENT_RE = re.compile(
    r"\b("
    r"save|store|keep|remember|add|register|record|bookmark|"
    r"sauve|sauver|enregistre|enregistrer|garde|garder|"
    r"ajiye|adana|fipamo|pamo|toju|chekwa|debe"
    r")\b",
    re.IGNORECASE,
)
_BENEFICIARY_ALIAS_CAPTURE_PATTERNS = (
    re.compile(
        r"\b(?:save|store|keep|remember|add|register|record|bookmark|"
        r"sauve|sauver|enregistre|enregistrer|garde|garder|"
        r"ajiye|adana|fipamo|pamo|toju|chekwa|debe)\b"
        r"(?:[\w\s]{0,32})?"
        r"\b(?:as|alias|name|called|oruko|suna|aha|nom)\b[:\s\"'`-]*(?P<alias>.+)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:save|store|keep|remember|add|register|record|bookmark|"
        r"sauve|sauver|enregistre|enregistrer|garde|garder|"
        r"ajiye|adana|fipamo|pamo|toju|chekwa|debe)\b[:\s\"'`-]+(?P<alias>.+)$",
        re.IGNORECASE,
    ),
)
_BENEFICIARY_QUOTED_ALIAS_RE = re.compile(r"[\"'](?P<alias>[^\"']{1,64})[\"']")
_BENEFICIARY_ALIAS_TRAILING_NOISE_RE = re.compile(
    r"\b("
    r"please|pls|abeg|thanks|thank you|thankyou|na|jare|biko|"
    r"don allah|jowo|s'il vous plait|sil vous plait|svp|stp"
    r")\b$",
    re.IGNORECASE,
)
_BENEFICIARY_ALIAS_ONLY_BLOCKLIST = {
    "it",
    "this",
    "that",
    "beneficiary",
    "recipient",
    "save",
    "yes",
    "okay",
    "ok",
    "sure",
}
_BENEFICIARY_BARE_ALIAS_BLOCKLIST = {
    "i",
    "im",
    "i'm",
    "me",
    "you",
    "your",
    "he",
    "she",
    "we",
    "they",
    "save",
    "call",
    "use",
    "name",
    "alias",
}
_BENEFICIARY_ALIAS_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9][A-Za-zÀ-ÖØ-öø-ÿ0-9'.-]*")
_SUGGESTION_TX_HINT_KEYWORDS = (
    "send",
    "transfer",
    "pay",
    "buy",
    "airtime",
    "data",
    "bundle",
    "fund",
    "withdraw",
    "balance",
    "statement",
    "transaction",
)
_SUGGESTION_TX_AMOUNT_PATTERN = re.compile(r"(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKmMhH]?\b")
_SUGGESTION_TX_ACCOUNT_PATTERN = re.compile(r"(?:\+?234|0)?(?:[\s().-]*\d){10,13}")
_SUGGESTION_TX_BANK_NETWORK_PATTERN = re.compile(
    r"\b(bank|first bank|opay|palmpay|kuda|zenith|gtb|gtbank|uba|fidelity|access|"
    r"mtn|glo|airtel|9mobile)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class BeneficiarySuggestionDecision:
    action: Literal["save_default", "save_alias", "dismiss"]
    alias: str | None = None
    reason: str = "unknown"


def _next_direct_account_task_id(existing_tasks: dict[str, TaskSpec]) -> str:
    idx = 1
    task_id = "direct_account_balance"
    while task_id in existing_tasks:
        idx += 1
        task_id = f"direct_account_balance_{idx}"
    return task_id


def _next_direct_query_task_id(existing_tasks: dict[str, TaskSpec]) -> str:
    idx = 1
    task_id = "direct_query"
    while task_id in existing_tasks:
        idx += 1
        task_id = f"direct_query_{idx}"
    return task_id


def _next_direct_domain_task_id(existing_tasks: dict[str, TaskSpec], domain: str) -> str:
    idx = 1
    task_id = f"direct_{domain}"
    while task_id in existing_tasks:
        idx += 1
        task_id = f"direct_{domain}_{idx}"
    return task_id


def _semantic_route_decision(route: Any) -> str | None:
    decision = str(getattr(route, "decision", "") or "")
    return decision or None


def _semantic_route_mode(route: Any) -> str | None:
    mode = getattr(route, "mode", None)
    if isinstance(mode, str) and mode:
        return mode
    return None


def _build_direct_domain_task(
    *,
    state: OrchestratorState,
    domain: Literal["query", "account", "support", "beneficiary", "transfer", "airtime", "data"],
    mode: str | None = None,
) -> tuple[str, TaskSpec]:
    if domain == "query":
        task_id = _next_direct_query_task_id(state.tasks)
    else:
        task_id = _next_direct_domain_task_id(state.tasks, domain)

    payload: dict[str, Any] = {
        "message": state.last_message_text,
        "instruction": state.last_message_text,
    }
    if domain == "query":
        if mode == "new":
            payload["force_new_query"] = True
    elif domain == "beneficiary":
        payload["action"] = "list_beneficiaries"
        payload["intent"] = "list_beneficiaries"
        payload["list_intent"] = True

    spec = TaskSpec(
        id=task_id,
        type=domain,
        stage=TaskStage.DRAFT,
        payload=payload,
    )
    return task_id, spec


def _direct_domain_capability_block_message(
    state: OrchestratorState,
    domain: str,
) -> str | None:
    action = DIRECT_DOMAIN_ACTIONS.get(domain)
    if not action:
        return None
    return capability_block_message(domain=domain, action=action, locale=_current_locale(state))


def _locale_update(state: OrchestratorState, locale: str) -> dict[str, Any]:
    loaded_context = dict(state.loaded_context or {})
    loaded_context["language"] = locale
    loaded_context["detected_language"] = locale
    return {"loaded_context": loaded_context}


def _current_locale(state: OrchestratorState) -> str:
    return LocaleManager.normalize((state.loaded_context or {}).get("language")).value


def _recent_batch_identity_for_state(state: OrchestratorState) -> str | None:
    for value in (state.channel_identity, state.phone_number, state.user_id):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _support_user_id_for_state(state: OrchestratorState) -> str:
    loaded_user_id = (state.loaded_context or {}).get("user_id") if isinstance(state.loaded_context, dict) else None
    for value in (loaded_user_id, state.user_id, state.phone_number):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _has_receipt_thread_candidates(receipt_thread_state: Any) -> bool:
    if receipt_thread_state is None:
        return False
    candidates = getattr(receipt_thread_state, "candidates", None)
    return isinstance(candidates, list) and bool(candidates)


def _looks_like_receipt_request(message_text: str) -> bool:
    return bool(_RECEIPT_REQUEST_RE.search(message_text or ""))


def _looks_like_receipt_selector_followup(message_text: str) -> bool:
    return bool(_RECEIPT_SELECTOR_FOLLOWUP_RE.search(message_text or ""))


def _normalize_user_text(message_text: str) -> str:
    return re.sub(r"\s+", " ", message_text.strip().lower()).rstrip("?.!,")


def _allow_phrase_heavy_fastpath(message_text: str, locale: str) -> bool:
    normalized = _normalize_user_text(message_text)
    if not normalized:
        return False
    if locale == "en":
        return True
    return bool(_ENGLISH_FASTPATH_CUE_RE.match(normalized))


def _resolve_explicit_language_switch(message_text: str) -> str | None:
    normalized = _normalize_user_text(message_text)
    if not normalized:
        return None
    requested = _LANGUAGE_SWITCH_EXACT.get(normalized)
    if requested in _SUPPORTED_SWITCHABLE_LOCALES:
        return requested
    return None


def _looks_like_language_switch_request(message_text: str, requested_locale: str | None = None) -> bool:
    if _resolve_explicit_language_switch(message_text) is not None:
        return True

    normalized = _normalize_user_text(message_text)
    if not normalized:
        return False

    locale_aliases = {
        "en": ("english",),
        "pcm": ("pidgin", "naija"),
        "yo": ("yoruba",),
        "ha": ("hausa",),
        "ig": ("igbo", "ibo"),
    }
    target_aliases = locale_aliases.get(requested_locale or "", ())
    if not target_aliases:
        target_aliases = tuple(alias for aliases in locale_aliases.values() for alias in aliases)

    has_locale_name = any(re.search(rf"\b{re.escape(alias)}\b", normalized) for alias in target_aliases)
    if not has_locale_name:
        return False

    return bool(re.search(r"\b(?:switch|speak|reply|continue|use|talk|chat|yarn|answer)\b", normalized))


async def _effective_response_locale(
    *,
    state: OrchestratorState,
    redis_client: Any | None,
    detected_language: str | None,
) -> tuple[str, dict[str, Any]]:
    locale = _current_locale(state)
    if not detected_language:
        return locale, {}

    detected_locale = LocaleManager.from_detection(detected_language).value
    if detected_locale == locale:
        return locale, {}

    if redis_client and await LocaleManager.is_explicit_locale(state.phone_number):
        return locale, {}

    return detected_locale, _locale_update(state, detected_locale)


def _should_invoke_semantic_router(message_text: str) -> bool:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower())
    return bool(normalized)


def classify_deterministic_meta_response(message_text: str) -> tuple[str, str | None] | None:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower()).rstrip("?.!,")
    if normalized in DETERMINISTIC_LOCALE_META_EXACT:
        return DETERMINISTIC_LOCALE_META_EXACT[normalized]
    for pattern, response in DETERMINISTIC_LOCALE_META_PATTERNS:
        if pattern.match(normalized):
            return response
    if normalized in DETERMINISTIC_GREETING_EXACT:
        return "conversational.greeting", None
    if normalized in DETERMINISTIC_APPRECIATION_EXACT:
        return "conversational.appreciation", None
    if normalized in DETERMINISTIC_CHECKIN_EXACT:
        return "conversational.checkin", None
    if normalized in DETERMINISTIC_IDENTITY_EXACT:
        return "conversational.identity", None
    if normalized in DETERMINISTIC_BRAND_ORIGIN_EXACT:
        return "conversational.brand_origin", None
    if normalized in DETERMINISTIC_CAPABILITY_EXACT:
        return "conversational.capability_question", None
    if any(pattern.match(normalized) for pattern in DETERMINISTIC_CAPABILITY_PATTERNS):
        return "conversational.capability_question", None
    return None


def _build_semantic_router_context(
    summary: TurnContextSummary,
    expected_executors: list[str],
    *,
    message_text: str,
) -> str:
    include_account_preview, include_beneficiary_preview = _semantic_router_preview_policy(message_text)
    return build_router_context_from_summary(
        summary,
        expected_executors=expected_executors,
        include_account_preview=include_account_preview,
        include_beneficiary_preview=include_beneficiary_preview,
    )


def _is_account_balance_request(message_text: str) -> bool:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower())
    if not normalized:
        return False
    candidate = BALANCE_DIRECT_CANCEL_PREFIX_RE.sub("", normalized)
    if any(re.search(pattern, candidate) for pattern in BALANCE_DIRECT_TRANSACTION_HINT_PATTERNS):
        return False
    return any(re.search(pattern, candidate) for pattern in ACCOUNT_BALANCE_REQUEST_PATTERNS)


def _is_query_domain_request(message_text: str) -> bool:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower()).rstrip("?.!,")
    if not normalized:
        return False
    return any(re.search(pattern, normalized) for pattern in _QUERY_DOMAIN_PATTERNS)


def _is_structural_query_domain_request(message_text: str) -> bool:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower()).rstrip("?.!,")
    if not normalized:
        return False
    return any(re.search(pattern, normalized) for pattern in _STRUCTURAL_QUERY_DIRECT_PATTERNS)


def _is_account_domain_request(message_text: str) -> bool:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower()).rstrip("?.!,")
    if not normalized:
        return False
    if _is_account_balance_request(normalized):
        return False
    if _is_query_domain_request(normalized):
        return False
    return any(re.search(pattern, normalized) for pattern in ACCOUNT_DOMAIN_PATTERNS)


def _is_beneficiary_domain_request(message_text: str) -> bool:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower()).rstrip("?.!,")
    if not normalized:
        return False
    return any(re.search(pattern, normalized) for pattern in BENEFICIARY_DOMAIN_PATTERNS)


def _is_obvious_airtime_request(message_text: str) -> bool:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower()).rstrip("?.!,")
    if not normalized or not _AIRTIME_DIRECT_PREFIX_RE.search(normalized):
        return False
    if _AIRTIME_DIRECT_SEND_PREFIX_RE.match(normalized) and not _AIRTIME_DIRECT_EXPLICIT_HINT_RE.search(normalized):
        return False
    has_mixed_clause = any(marker in normalized for marker in SEMANTIC_ROUTER_MULTI_CLAUSE_MARKERS)
    if has_mixed_clause and _TRANSFER_DIRECT_NON_TRANSFER_RE.search(normalized):
        return False
    return bool(_TRANSFER_DIRECT_AMOUNT_RE.search(normalized) and _AIRTIME_DIRECT_HINT_RE.search(normalized))


def _is_obvious_data_request(message_text: str) -> bool:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower()).rstrip("?.!,")
    if not normalized or not _DATA_DIRECT_PREFIX_RE.search(normalized):
        return False
    has_mixed_clause = any(marker in normalized for marker in SEMANTIC_ROUTER_MULTI_CLAUSE_MARKERS)
    if has_mixed_clause and _TRANSFER_DIRECT_NON_TRANSFER_RE.search(normalized):
        return False
    return bool(_DATA_DIRECT_HINT_RE.search(normalized))


def _semantic_router_preview_policy(message_text: str) -> tuple[bool, bool]:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower())
    if not normalized:
        return False, False

    include_accounts = bool(
        re.search(r"\b(?:account|accounts|bank|banks|balance|default|unlink|link|ready)\b", normalized)
    )
    include_beneficiaries = bool(re.search(r"\b(?:beneficiar|recipient|saved|alias)\b", normalized))
    return include_accounts, include_beneficiaries


def _build_direct_context_recap_response(summary: TurnContextSummary) -> str | None:
    if summary.active_flow_intent:
        next_step = "Continue with that flow."
        if summary.active_flow_missing_fields:
            next_step = f"Next step: provide {', '.join(summary.active_flow_missing_fields)}."
        return f"We are still in your {summary.active_flow_intent} flow. {next_step}"
    if summary.query_session_active:
        return "You are still viewing transaction results. You can refine the query or ask a follow-up."
    if summary.recent_answer_focus:
        return f"The last thing I showed was your {summary.recent_answer_focus.replace('_', ' ')}."
    return None


def _is_direct_context_recap_request(message_text: str) -> bool:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower()).rstrip("?.!,")
    return normalized in _DIRECT_CONTEXT_RECAP_EXACT


def _has_explicit_cancel(message_text: str) -> bool:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower())
    if not normalized:
        return False
    return any(re.search(pattern, normalized) for pattern in EXPLICIT_CANCEL_PATTERNS)


def _has_pending_mandate_without_ready_accounts(loaded_context: dict[str, Any] | None) -> bool:
    if not isinstance(loaded_context, dict):
        return False
    accounts_raw = loaded_context.get("accounts")
    if not isinstance(accounts_raw, list):
        return False

    has_ready = False
    has_pending_like = False
    for account in accounts_raw:
        if not isinstance(account, dict):
            continue
        status = str(account.get("mandate_status") or "").strip().lower()
        if not status:
            continue
        if status == "ready":
            has_ready = True
        else:
            has_pending_like = True

    return has_pending_like and not has_ready


def _looks_like_multi_recipient_transfer(normalized: str) -> bool:
    if "split" in normalized or " each " in f" {normalized} " or re.search(r"\b(?:between|btw)\b", normalized):
        return True
    if len(_TRANSFER_DIRECT_AMOUNT_RE.findall(normalized)) >= 2:
        return True
    if not _TRANSFER_MULTI_RECIPIENT_TAIL_RE.search(normalized):
        return False
    if _TRANSFER_DIRECT_NON_TRANSFER_RE.search(normalized):
        return False
    return True


def _classify_obvious_transfer_request(message_text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower()).rstrip("?.!,")
    if not normalized or not _TRANSFER_DIRECT_PREFIX_RE.search(normalized):
        return None
    if _TRANSFER_DIRECT_QUERY_MARKER_RE.search(normalized) and not _TRANSFER_DIRECT_PREFIX_RE.match(normalized):
        return None
    has_multi_clause = any(marker in normalized for marker in SEMANTIC_ROUTER_MULTI_CLAUSE_MARKERS)
    if has_multi_clause and _TRANSFER_DIRECT_NON_TRANSFER_RE.search(normalized):
        return None
    if _is_account_balance_request(normalized) or _is_query_domain_request(normalized):
        return None
    if _PHONE_NUMBER_CUE_RE.search(normalized):
        return None
    if _TRANSFER_DIRECT_NON_TRANSFER_RE.search(normalized) and not _TRANSFER_DIRECT_RECIPIENT_CUE_RE.search(normalized):
        return None

    if _looks_like_multi_recipient_transfer(normalized):
        return "batch_transfer_command"
    if _TRANSFER_DIRECT_PERCENTAGE_RE.search(normalized) or _TRANSFER_DIRECT_SOURCE_RE.search(normalized):
        return "account_aware_transfer_command"

    has_amount_like = bool(_TRANSFER_DIRECT_AMOUNT_RE.search(normalized))
    has_transfer_shape = bool(_TRANSFER_DIRECT_RECIPIENT_CUE_RE.search(normalized))
    if not has_amount_like:
        return None
    if not has_transfer_shape:
        return "fresh_transfer_missing_recipient_command"

    return "fresh_transfer_command"


def _obvious_mixed_transaction_executors(message_text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower()).rstrip("?.!,")
    if not normalized:
        return []

    executors: list[str] = []
    if _MIXED_TRANSFER_CLAUSE_RE.search(normalized):
        executors.append("transfer")
    if _MIXED_AIRTIME_CLAUSE_RE.search(normalized):
        executors.append("airtime")
    if _MIXED_DATA_CLAUSE_RE.search(normalized):
        executors.append("data")
    if len(executors) < 2:
        return []
    return executors


def classify_obvious_transfer_request(message_text: str, *, locale: str | None = None) -> str | None:
    """Public helper for pre-graph fast-path hints."""
    if locale and not _allow_phrase_heavy_fastpath(message_text, LocaleManager.normalize(locale).value):
        return None
    return _classify_obvious_transfer_request(message_text)


def _build_query_session_exit_updates(
    state: OrchestratorState,
    *,
    query_session_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    has_query_snapshot = isinstance(query_session_snapshot, dict) and bool(query_session_snapshot.get("session_active"))
    has_query_session_stack = any(session.domain == "query" for session in state.session_stack)
    if not has_query_snapshot and not has_query_session_stack and state.active_domain != "query":
        return {}
    remaining_sessions = [session for session in state.session_stack if session.domain != "query"]
    return {
        "stashed_query_session": None,
        "session_stack": remaining_sessions,
        "active_domain": None if state.active_domain == "query" else state.active_domain,
    }

def _normalize_suggestion_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _is_transaction_like_message(normalized_text: str) -> bool:
    if not normalized_text:
        return False
    if any(keyword in normalized_text for keyword in _SUGGESTION_TX_HINT_KEYWORDS):
        return True
    if _SUGGESTION_TX_AMOUNT_PATTERN.search(normalized_text):
        return True
    if _SUGGESTION_TX_ACCOUNT_PATTERN.search(normalized_text):
        return True
    return bool(_SUGGESTION_TX_BANK_NETWORK_PATTERN.search(normalized_text))


def _cleanup_alias(value: str) -> str | None:
    alias = value.strip().strip(".,;:!?- ")
    if not alias:
        return None

    alias = re.sub(r"^(?:is|na|be|named|called)\s+", "", alias, flags=re.IGNORECASE).strip()
    while True:
        cleaned = _BENEFICIARY_ALIAS_TRAILING_NOISE_RE.sub("", alias).strip(" .,;:!?-")
        if cleaned == alias:
            break
        alias = cleaned
    if not alias:
        return None
    if len(alias) > _BENEFICIARY_SUGGESTION_ALIAS_MAX_CHARS:
        alias = alias[:_BENEFICIARY_SUGGESTION_ALIAS_MAX_CHARS].rstrip()
    alias_token = alias.lower()
    if alias_token in _BENEFICIARY_ALIAS_ONLY_BLOCKLIST:
        return None
    return alias


def _extract_alias_from_text(message_text: str) -> str | None:
    quoted_match = _BENEFICIARY_QUOTED_ALIAS_RE.search(message_text)
    if quoted_match:
        alias = _cleanup_alias(quoted_match.group("alias"))
        if alias:
            return alias

    for pattern in _BENEFICIARY_ALIAS_CAPTURE_PATTERNS:
        match = pattern.search(message_text)
        if not match:
            continue
        alias = _cleanup_alias(match.group("alias"))
        if alias:
            return alias

    normalized = _normalize_suggestion_text(message_text)
    for marker in _BENEFICIARY_ALIAS_MARKERS:
        if marker not in normalized:
            continue
        prefix, suffix = normalized.rsplit(marker, 1)
        if not suffix or not prefix:
            continue
        if not _BENEFICIARY_SAVE_INTENT_RE.search(prefix):
            continue
        marker_idx = prefix.rfind(marker.strip())
        if marker_idx == -1:
            continue
        candidate = message_text[-len(suffix) :]
        alias = _cleanup_alias(candidate)
        if alias:
            return alias

    return None


def _is_bare_alias_candidate(message_text: str, alias: str) -> bool:
    normalized = _normalize_suggestion_text(message_text)
    if not normalized:
        return False
    if any(marker in normalized for marker in SEMANTIC_ROUTER_MULTI_CLAUSE_MARKERS):
        return False
    words = alias.split()
    if not words or len(words) > _BENEFICIARY_BARE_ALIAS_MAX_WORDS:
        return False
    lowered_words = {word.lower() for word in words}
    if lowered_words & _BENEFICIARY_BARE_ALIAS_BLOCKLIST:
        return False
    return all(_BENEFICIARY_ALIAS_TOKEN_RE.fullmatch(word) for word in words)


def _resolve_beneficiary_suggestion_reply(
    message_text: str,
    *,
    locale: str,
    suggestion_payload: dict[str, Any] | None,
) -> BeneficiarySuggestionDecision:
    del locale, suggestion_payload
    normalized = _normalize_suggestion_text(message_text)
    if not normalized:
        return BeneficiarySuggestionDecision(action="dismiss", reason="empty_message")

    if _is_transaction_like_message(normalized):
        return BeneficiarySuggestionDecision(action="dismiss", reason="transaction_guard")

    if normalized in _BENEFICIARY_DISMISS_PHRASES:
        return BeneficiarySuggestionDecision(action="dismiss", reason="explicit_dismiss")

    has_save_intent = bool(_BENEFICIARY_SAVE_INTENT_RE.search(normalized))
    extracted_alias = _extract_alias_from_text(message_text)
    if has_save_intent and extracted_alias:
        return BeneficiarySuggestionDecision(action="save_alias", alias=extracted_alias, reason="explicit_save_alias")
    if has_save_intent:
        return BeneficiarySuggestionDecision(action="save_default", reason="explicit_save")

    if normalized in _BENEFICIARY_SAVE_AFFIRMATIONS:
        return BeneficiarySuggestionDecision(action="save_default", reason="pure_affirmation")

    bare_alias = _cleanup_alias(message_text)
    if bare_alias and _is_bare_alias_candidate(message_text, bare_alias):
        return BeneficiarySuggestionDecision(action="save_alias", alias=bare_alias, reason="bare_alias_reply")

    return BeneficiarySuggestionDecision(action="dismiss", reason="ambiguous_dismiss")


def _next_direct_beneficiary_task_id(existing_tasks: dict[str, TaskSpec]) -> str:
    idx = 1
    task_id = "direct_beneficiary_save"
    while task_id in existing_tasks:
        idx += 1
        task_id = f"direct_beneficiary_save_{idx}"
    return task_id


def _route_observability_updates(
    *,
    owner: str,
    decision: str,
    target_domain: str | None = None,
    mode: str | None = None,
    route_source: str | None = None,
    heuristic_type: str | None = None,
    heuristic_name: str | None = None,
) -> dict[str, Any]:
    return {
        "routing_owner": owner,
        "routing_decision": decision,
        "routing_target_domain": target_domain,
        "routing_mode": mode,
        "route_source": route_source or owner,
        "routing_heuristic_type": heuristic_type,
        "routing_heuristic_name": heuristic_name,
    }


def _pending_interrupt_task_types(state: OrchestratorState) -> set[str]:
    interrupt = state.pending_interrupt
    if interrupt is None or not isinstance(getattr(interrupt, "task_ids", None), list):
        return set()
    return {
        state.tasks[task_id].type
        for task_id in interrupt.task_ids
        if isinstance(task_id, str) and task_id in state.tasks
    }


def _has_live_pending_interrupt(state: OrchestratorState) -> bool:
    interrupt = state.pending_interrupt
    if interrupt is None:
        return False

    task_types = _pending_interrupt_task_types(state)
    if not task_types:
        return False

    interrupt_kind = getattr(interrupt, "kind", None)
    if interrupt_kind in {"confirmation", "auth"}:
        return True
    if interrupt_kind != "input":
        return True

    # Active query sessions own their own follow-up semantics and should not pay interrupt-router cost.
    return any(task_type != "query" for task_type in task_types)


def _is_numeric_input_interrupt_selection(state: OrchestratorState, message_text: str) -> bool:
    interrupt = state.pending_interrupt
    if interrupt is None or getattr(interrupt, "kind", None) != "input":
        return False
    if not message_text.strip().isdigit():
        return False

    task_ids = getattr(interrupt, "task_ids", None) or []
    if len(task_ids) != 1:
        return False

    fields_by_task = getattr(interrupt, "fields_by_task", None) or {}
    required_fields = fields_by_task.get(task_ids[0]) or []
    return set(required_fields) == {"source_account_id"}


def _query_followup_bypass_reason(
    *,
    message_text: str,
    locale: str,
    query_session_snapshot: dict[str, Any] | None,
    has_context_frames: bool = False,
) -> tuple[str | None, str | None]:
    if not isinstance(query_session_snapshot, dict) or not query_session_snapshot.get("session_active"):
        return None, None

    transfer_request_reason = _classify_obvious_transfer_request(message_text)
    if transfer_request_reason or _is_obvious_airtime_request(message_text) or _is_obvious_data_request(message_text):
        return None, "fresh_transaction_request"

    shortcut, miss_reason = resolve_query_shortcut_with_reason(message_text, locale)
    if shortcut is not None:
        return "query_shortcut", shortcut.action

    if query_session_snapshot.get("pending_clarification"):
        parsed_time_range = QueryParser.parse_clarification_time_range(message_text, today=lagos_today())
        if parsed_time_range is not None:
            return "pending_clarification", parsed_time_range.period or "days_back"
        return None, miss_reason

    if has_context_frames:
        return "active_query_session", miss_reason or "query_session_active"
    return None, miss_reason






async def session_gate_direct_path(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    from apps.chat.src.agent.orchestrator.nodes.gate.pipeline import (
        _GATE_STAGES,
        GateContext,
        _stage_stale_interrupt_cleanup,
    )
    """
    Direct-path gate.

    Applies deterministic guardrails first, then delegates first-pass semantic routing
    to the semantic router, and falls through to planner only for planner-owned routes.

    Architecture: a pipeline of focused stage functions. Each stage returns a dict
    (short-circuit with a gate response) or None (continue to the next stage).
    """
    session = state.session_stack[-1] if state.session_stack else None
    logger.info(
        "gate_entry", session_domain=session.domain if session else None, interrupt=state.pending_interrupt is not None
    )

    message_text = (state.last_message_text or "").strip()
    ctx = GateContext(
        state=state,
        config=config,
        redis_client=config["configurable"].get("redis_client"),
        task_planner=config["configurable"].get("task_planner"),
        conversation_responder=config["configurable"].get("conversation_responder"),
        message_text=message_text,
        current_locale=_current_locale(state),
        gate_updates={},
        live_pending_interrupt=_has_live_pending_interrupt(state),
        phrase_heavy_fastpath_allowed=_allow_phrase_heavy_fastpath(message_text, _current_locale(state)),
        should_invoke_semantic_router_fn=_should_invoke_semantic_router,
    )

    _stage_stale_interrupt_cleanup(ctx)

    for stage in _GATE_STAGES:
        result = await stage(ctx)
        if result is not None:
            return result

    logger.info("gate_dispatch_to_planner", reason="planner_owned_or_unresolved_route")
    return {
        **ctx.gate_updates,
        **(ctx.summary_updates or {}),
        **_route_observability_updates(owner="planner", decision="planner_handoff"),
    }
