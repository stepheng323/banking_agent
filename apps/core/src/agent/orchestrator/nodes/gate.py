"""Session Gate Node.

Applies deterministic guardrails, invokes the semantic router, and only falls through
to planner for planner-owned routes.
"""

import re
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.graphs.query.services.parser import QueryParser
from apps.core.src.agent.graphs.query.services.query_shortcuts import resolve_query_shortcut_with_reason
from apps.core.src.agent.graphs.query.utils.timezone import lagos_today
from apps.core.src.agent.orchestrator.conversational_style import format_out_of_scope_reply
from apps.core.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.cancellation import (
    build_cancellation_reset_updates,
    cancelled_message,
    clarify_message,
    clear_query_session,
    has_cancelable_state,
    is_explicit_cancel_message,
)
from apps.core.src.agent.orchestrator.nodes.planner_context import (
    TurnContextSummary,
    _load_query_session_snapshot,
    build_router_context_from_summary,
    get_or_build_turn_context_summary,
)
from shared.i18n import LocaleManager, render_locale_switched, render_message
from shared.services.async_completion import get_recent_batch_reference
from shared.utils.logging import get_logger

logger = get_logger(__name__)

TRANSACTION_EXECUTORS = {"transfer", "airtime", "data"}
SEMANTIC_ROUTER_MULTI_CLAUSE_MARKERS = (" and ", " & ", " then ", ",")
_TRANSFER_DIRECT_PREFIX_RE = re.compile(
    r"^(?:(?:ok(?:ay)?|please|pls|abeg|oya|jowo|biko|kindly)\s+)*"
    r"(?:send|transfer|pay|remit|split|fi|tura)\b",
    re.IGNORECASE,
)
_TRANSFER_DIRECT_RECIPIENT_CUE_RE = re.compile(r"\b(?:to|for|between|btw|si|zuwa)\b", re.IGNORECASE)
_TRANSFER_DIRECT_AMOUNT_RE = re.compile(r"(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKmMhH]?\b")
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
    "how are you",
    "how you dey",
    "how body",
}
DETERMINISTIC_IDENTITY_EXACT = {
    "who are you",
    "what is your name",
    "what s your name",
    "what's your name",
}
DETERMINISTIC_BRAND_ORIGIN_EXACT = {
    "who created you",
    "who built you",
    "who made you",
    "what does narya mean",
    "what is narya",
}
DETERMINISTIC_CAPABILITY_EXACT = {
    "what can you do",
    "what do you do",
    "what can you help me with",
    "what do you handle",
}
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
    r"\b(?:airtime|mtn|glo|airtel|9mobile)\b|(?:\+?234|0)?(?:[\s().-]*\d){10,13}",
    re.IGNORECASE,
)
_DATA_DIRECT_PREFIX_RE = re.compile(
    r"^(?:(?:ok(?:ay)?|please|pls|abeg|oya|jowo|biko|kindly)\s+)*"
    r"(?:buy|get|send)\b",
    re.IGNORECASE,
)
_DATA_DIRECT_HINT_RE = re.compile(
    r"\b(?:data|bundle)\b|\d+\s*(?:mb|gb)\b|(?:\+?234|0)?(?:[\s().-]*\d){10,13}",
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
_RECEIPT_REQUEST_RE = re.compile(
    r"\b(?:receipt|proof\s+of\s+payment|payment\s+receipt|show\s+receipt|send\s+receipt)\b",
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


def _looks_like_receipt_request(message_text: str) -> bool:
    return bool(_RECEIPT_REQUEST_RE.search(message_text or ""))


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
) -> dict[str, Any]:
    return {
        "routing_owner": owner,
        "routing_decision": decision,
        "routing_target_domain": target_domain,
        "routing_mode": mode,
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
) -> tuple[str | None, str | None]:
    if not isinstance(query_session_snapshot, dict) or not query_session_snapshot.get("session_active"):
        return None, None

    shortcut, miss_reason = resolve_query_shortcut_with_reason(message_text, locale)
    if shortcut is not None:
        return "query_shortcut", shortcut.action

    if query_session_snapshot.get("pending_clarification"):
        parsed_time_range = QueryParser.parse_clarification_time_range(message_text, today=lagos_today())
        if parsed_time_range is not None:
            return "pending_clarification", parsed_time_range.period or "days_back"
        return None, miss_reason

    return None, miss_reason


async def session_gate_direct_path(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    """
    Direct-path gate.

    Applies deterministic guardrails first, then delegates first-pass semantic routing
    to the semantic router, and falls through to planner only for planner-owned routes.
    """

    task_planner = config["configurable"].get("task_planner")
    redis_client = config["configurable"].get("redis_client")
    session = state.session_stack[-1] if state.session_stack else None

    logger.info(
        "gate_entry", session_domain=session.domain if session else None, interrupt=state.pending_interrupt is not None
    )

    message_text = (state.last_message_text or "").strip()
    current_locale = _current_locale(state)
    phrase_heavy_fastpath_allowed = _allow_phrase_heavy_fastpath(message_text, current_locale)
    gate_updates: dict[str, Any] = {}
    live_pending_interrupt = _has_live_pending_interrupt(state)
    if state.pending_interrupt is not None and not live_pending_interrupt:
        logger.info(
            "interrupt_router_skipped_no_live_flow",
            kind=getattr(state.pending_interrupt, "kind", None),
            task_ids=getattr(state.pending_interrupt, "task_ids", None),
            current_task_types=sorted(_pending_interrupt_task_types(state)),
        )
        gate_updates["pending_interrupt"] = None

    requested_locale = _resolve_explicit_language_switch(message_text)
    if requested_locale is not None:
        if redis_client:
            resolved = await LocaleManager.set_locale(
                state.phone_number,
                requested_locale,
                source="user_command",
            )
            next_locale = resolved.value
        else:
            next_locale = requested_locale
        logger.info("gate_explicit_language_switch", locale=next_locale)
        return {
            **gate_updates,
            "direct_path_triggered": True,
            "final_response": render_locale_switched(next_locale),
            **_locale_update(state, next_locale),
            **_route_observability_updates(owner="guardrail", decision="language_switch"),
        }

    if is_explicit_cancel_message(message_text):
        if has_cancelable_state(state):
            cleanup_updates = await build_cancellation_reset_updates(state, redis_client)
            return {
                **gate_updates,
                **cleanup_updates,
                "direct_path_triggered": True,
                "final_response": cancelled_message(state, current_locale),
                **_route_observability_updates(owner="guardrail", decision="cancel"),
            }
        query_session_snapshot, _ = await _load_query_session_snapshot(state, redis_client)
        if (
            isinstance(query_session_snapshot, dict)
            and query_session_snapshot.get("session_active")
            and query_session_snapshot.get("pending_clarification")
        ):
            await clear_query_session(redis_client, state.phone_number)
            return {
                **gate_updates,
                "direct_path_triggered": True,
                "final_response": render_message("query.session.goodbye", current_locale),
                **_route_observability_updates(owner="guardrail", decision="cancel"),
            }
        if _has_pending_mandate_without_ready_accounts(state.loaded_context):
            logger.info("gate_pending_mandate_notice_dismissed")
            return {
                **gate_updates,
                "direct_path_triggered": True,
                "final_response": cancelled_message(state, current_locale),
                **_route_observability_updates(owner="guardrail", decision="cancel_pending_mandate_notice"),
            }
        return {
            **gate_updates,
            "direct_path_triggered": True,
            "final_response": clarify_message(state, current_locale),
            **_route_observability_updates(owner="guardrail", decision="cancel"),
        }

    if not live_pending_interrupt and redis_client and _looks_like_receipt_request(message_text):
        recent_batch_identity = _recent_batch_identity_for_state(state)
        recent_batch = await get_recent_batch_reference(redis_client, identity=recent_batch_identity)
        if recent_batch is not None and recent_batch.get("legs"):
            task_id, spec = _build_direct_domain_task(state=state, domain="support")
            spec.payload["intent"] = "receipt_request"
            spec.payload["recent_batch_followup"] = True
            logger.info(
                "gate_recent_batch_receipt_support_handoff",
                task_id=task_id,
                async_group_id=recent_batch.get("async_group_id"),
                identity=recent_batch_identity,
            )
            return {
                **gate_updates,
                "tasks": {task_id: spec},
                "waves": [[task_id]],
                "current_wave_index": 0,
                "planner_output": None,
                "pending_interrupt": None,
                "direct_path_triggered": True,
                "semantic_path_shape": "support_receipt_direct",
                **_route_observability_updates(
                    owner="guardrail",
                    decision="recent_batch_receipt_support",
                    target_domain="support",
                ),
            }

    if not live_pending_interrupt and redis_client:
        import json

        suggestion_key = f"user:{state.phone_number}:beneficiary_suggestion"
        try:
            suggestion_data = await redis_client.get(suggestion_key)
        except Exception as exc:
            logger.warning("beneficiary_suggestion_lookup_failed", error=str(exc))
            suggestion_data = None

        if suggestion_data:
            suggestion_payload: dict[str, Any] | None
            try:
                parsed_payload = json.loads(suggestion_data)
                suggestion_payload = parsed_payload if isinstance(parsed_payload, dict) else None
            except Exception:
                suggestion_payload = None

            decision = _resolve_beneficiary_suggestion_reply(
                message_text,
                locale=current_locale,
                suggestion_payload=suggestion_payload,
            )
            logger.info(
                "beneficiary_suggestion_gate_decision",
                decision=decision.action,
                reason=decision.reason,
                locale=current_locale,
                alias_present=bool(decision.alias),
            )
            if decision.action in {"save_default", "save_alias"}:
                task_id = _next_direct_beneficiary_task_id(state.tasks)
                task_payload: dict[str, Any] = {
                    "action": "save_beneficiary",
                    "instruction": state.last_message_text,
                    "message": state.last_message_text,
                }
                if decision.alias:
                    task_payload["alias"] = decision.alias
                spec = TaskSpec(
                    id=task_id,
                    type="beneficiary",
                    stage=TaskStage.DRAFT,
                    payload=task_payload,
                )
                return {
                    **gate_updates,
                    "tasks": {task_id: spec},
                    "waves": [[task_id]],
                    "current_wave_index": 0,
                    "planner_output": None,
                    "pending_interrupt": None,
                    "direct_path_triggered": True,
                    **_route_observability_updates(
                        owner="guardrail",
                        decision="beneficiary_save",
                        target_domain="beneficiary",
                        mode="new",
                    ),
                }

            try:
                await redis_client.delete(suggestion_key)
            except Exception as exc:
                logger.warning("beneficiary_suggestion_dismiss_delete_failed", error=str(exc))
            else:
                logger.info("beneficiary_suggestion_dismissed", reason=decision.reason)

    if not live_pending_interrupt and phrase_heavy_fastpath_allowed and _is_account_balance_request(message_text):
        cleanup_updates: dict[str, Any] = {}
        if _has_explicit_cancel(message_text):
            cleanup_updates = await build_cancellation_reset_updates(state, redis_client)

        task_id = _next_direct_account_task_id(state.tasks)
        spec = TaskSpec(
            id=task_id,
            type="account",
            stage=TaskStage.DRAFT,
            payload={
                "action": "check_balance",
                "message": state.last_message_text,
                "instruction": state.last_message_text,
            },
        )
        logger.info("gate_direct_account_balance", task_id=task_id, with_cleanup=bool(cleanup_updates))
        return {
            **gate_updates,
            **cleanup_updates,
            "tasks": {task_id: spec},
            "waves": [[task_id]],
            "current_wave_index": 0,
            "planner_output": None,
            "pending_interrupt": None,
            "direct_path_triggered": True,
            **_route_observability_updates(
                owner="guardrail",
                decision="balance_direct",
                target_domain="account",
                mode="new",
            ),
        }

    if (
        not live_pending_interrupt
        and not state.has_quote
        and phrase_heavy_fastpath_allowed
        and _is_account_domain_request(message_text)
    ):
        cleanup_updates: dict[str, Any] = {}
        if _has_explicit_cancel(message_text):
            cleanup_updates = await build_cancellation_reset_updates(state, redis_client)
        task_id, spec = _build_direct_domain_task(state=state, domain="account", mode="new")
        logger.info("gate_deterministic_account_domain", task_id=task_id)
        return {
            **gate_updates,
            **cleanup_updates,
            "tasks": {task_id: spec},
            "waves": [[task_id]],
            "current_wave_index": 0,
            "planner_output": None,
            "pending_interrupt": None,
            "direct_path_triggered": True,
            "semantic_path_shape": "deterministic_account_domain",
            **_route_observability_updates(
                owner="guardrail",
                decision="deterministic_account_domain",
                target_domain="account",
                mode="new",
            ),
        }

    if (
        not live_pending_interrupt
        and not state.has_quote
        and phrase_heavy_fastpath_allowed
        and _is_beneficiary_domain_request(message_text)
    ):
        task_id, spec = _build_direct_domain_task(state=state, domain="beneficiary", mode="new")
        logger.info("gate_deterministic_beneficiary_domain", task_id=task_id)
        return {
            **gate_updates,
            "tasks": {task_id: spec},
            "waves": [[task_id]],
            "current_wave_index": 0,
            "planner_output": None,
            "pending_interrupt": None,
            "direct_path_triggered": True,
            "semantic_path_shape": "deterministic_beneficiary_domain",
            **_route_observability_updates(
                owner="guardrail",
                decision="deterministic_beneficiary_domain",
                target_domain="beneficiary",
                mode="new",
            ),
        }

    if (
        not live_pending_interrupt
        and not state.has_quote
        and phrase_heavy_fastpath_allowed
        and _is_obvious_airtime_request(message_text)
    ):
        task_id, spec = _build_direct_domain_task(state=state, domain="airtime", mode="new")
        logger.info("gate_deterministic_airtime_domain", task_id=task_id)
        return {
            **gate_updates,
            "tasks": {task_id: spec},
            "waves": [[task_id]],
            "current_wave_index": 0,
            "planner_output": None,
            "pending_interrupt": None,
            "direct_path_triggered": True,
            "semantic_path_shape": "deterministic_airtime_domain",
            **_route_observability_updates(
                owner="guardrail",
                decision="deterministic_airtime_domain",
                target_domain="airtime",
                mode="new",
            ),
        }

    if (
        not live_pending_interrupt
        and not state.has_quote
        and phrase_heavy_fastpath_allowed
        and _is_obvious_data_request(message_text)
    ):
        task_id, spec = _build_direct_domain_task(state=state, domain="data", mode="new")
        logger.info("gate_deterministic_data_domain", task_id=task_id)
        return {
            **gate_updates,
            "tasks": {task_id: spec},
            "waves": [[task_id]],
            "current_wave_index": 0,
            "planner_output": None,
            "pending_interrupt": None,
            "direct_path_triggered": True,
            "semantic_path_shape": "deterministic_data_domain",
            **_route_observability_updates(
                owner="guardrail",
                decision="deterministic_data_domain",
                target_domain="data",
                mode="new",
            ),
        }

    # --- PIN callback with no active session (checkpoint was cleaned) ---
    if state.pin_verified and not live_pending_interrupt:
        logger.warning("gate_pin_verified_no_session", reason="checkpoint_cleaned")
        return {
            **gate_updates,
            "direct_path_triggered": True,
            "final_response": render_message(
                "orchestrator.session.expired_pin",
                current_locale,
                fallback_en="Your transaction session has expired. Please start a new transaction.",
            ),
            **_route_observability_updates(owner="guardrail", decision="expired_pin_session"),
        }

    if not live_pending_interrupt and not state.has_quote:
        deterministic_meta = classify_deterministic_meta_response(message_text)
        if deterministic_meta:
            response_key, response_locale = deterministic_meta
            locale = response_locale or current_locale
            locale_updates = _locale_update(state, locale) if response_locale else {}
            query_session_snapshot, _ = await _load_query_session_snapshot(state, redis_client)
            exit_updates = _build_query_session_exit_updates(
                state,
                query_session_snapshot=query_session_snapshot,
            )
            if (
                redis_client
                and isinstance(query_session_snapshot, dict)
                and query_session_snapshot.get("session_active")
            ):
                await clear_query_session(redis_client, state.phone_number)
            if exit_updates:
                logger.info("gate_query_session_exited_on_direct_reply", had_pending_clarification=False)
            return {
                **gate_updates,
                **exit_updates,
                **locale_updates,
                "direct_path_triggered": True,
                "final_response": render_message(response_key, locale),
                "semantic_path_shape": "meta_direct",
                **_route_observability_updates(owner="guardrail", decision="meta_direct"),
            }

    summary_path_label = (
        "interrupt_path"
        if live_pending_interrupt
        else (
            "direct_path"
            if (
                not state.has_quote
                and callable(getattr(task_planner, "route_semantic_turn", None))
                and _should_invoke_semantic_router(message_text)
            )
            else "planner_path"
        )
    )
    query_session_snapshot, query_session_source = await _load_query_session_snapshot(state, redis_client)
    turn_summary, summary_updates = get_or_build_turn_context_summary(
        state,
        query_session_snapshot=query_session_snapshot,
        query_session_source=query_session_source,
        path_label=summary_path_label,
    )
    summary_updates = summary_updates or {}
    has_active_query_session = bool(
        isinstance(query_session_snapshot, dict) and query_session_snapshot.get("session_active")
    )
    has_query_session_stack = bool(session and session.domain == "query")
    logger.info(
        "gate_query_routing_breadcrumb",
        path="query_session_context",
        has_active_query_session=has_active_query_session or has_query_session_stack,
        query_session_source=query_session_source,
        query_session_stack=has_query_session_stack,
    )

    if not live_pending_interrupt and not state.has_quote and _is_direct_context_recap_request(message_text):
        response = _build_direct_context_recap_response(turn_summary)
        if response is not None:
            logger.info(
                "gate_direct_context_recap",
                focus=turn_summary.recent_answer_focus,
                active_flow=turn_summary.active_flow_intent,
            )
            return {
                **gate_updates,
                **summary_updates,
                "direct_path_triggered": True,
                "final_response": response,
                "semantic_path_shape": "direct_context_recap",
                **_route_observability_updates(owner="guardrail", decision="direct_context_recap"),
            }
        logger.info("gate_direct_context_recap_miss", reason="no_active_context")

    if not live_pending_interrupt and not state.has_quote:
        bypass_reason, bypass_detail = _query_followup_bypass_reason(
            message_text=message_text,
            locale=current_locale,
            query_session_snapshot=query_session_snapshot if isinstance(query_session_snapshot, dict) else None,
        )
        if bypass_reason is not None:
            logger.info(
                "query_followup_bypass_hit",
                reason=bypass_reason,
                detail=bypass_detail,
                query_session_source=query_session_source,
            )
            task_id, spec = _build_direct_domain_task(state=state, domain="query")
            return {
                **gate_updates,
                **summary_updates,
                "tasks": {task_id: spec},
                "waves": [[task_id]],
                "current_wave_index": 0,
                "planner_output": None,
                "direct_path_triggered": True,
                "semantic_path_shape": "query_followup_bypass",
                **_route_observability_updates(
                    owner="query_session",
                    decision="query_followup_bypass",
                    target_domain="query",
                    mode="continuation",
                ),
            }

    if (
        not live_pending_interrupt
        and not state.has_quote
        and not has_active_query_session
        and phrase_heavy_fastpath_allowed
        and _is_query_domain_request(message_text)
    ):
        task_id, spec = _build_direct_domain_task(state=state, domain="query", mode="new")
        logger.info("gate_deterministic_query_domain", task_id=task_id)
        return {
            **gate_updates,
            **summary_updates,
            "tasks": {task_id: spec},
            "waves": [[task_id]],
            "current_wave_index": 0,
            "planner_output": None,
            "direct_path_triggered": True,
            "semantic_path_shape": "deterministic_query_domain",
            **_route_observability_updates(
                owner="guardrail",
                decision="deterministic_query_domain",
                target_domain="query",
                mode="new",
            ),
        }

    if not live_pending_interrupt and not state.has_quote:
        transfer_request_reason = (
            _classify_obvious_transfer_request(message_text) if phrase_heavy_fastpath_allowed else None
        )
        if transfer_request_reason in {"fresh_transfer_command", "fresh_transfer_missing_recipient_command"}:
            transfer_updates: dict[str, Any] = {}
            if (
                isinstance(query_session_snapshot, dict)
                and query_session_snapshot.get("session_active")
            ):
                await clear_query_session(redis_client, state.phone_number)
                transfer_updates.update(
                    _build_query_session_exit_updates(
                        state,
                        query_session_snapshot=query_session_snapshot,
                    )
                )
            task_id, spec = _build_direct_domain_task(state=state, domain="transfer", mode="new")
            logger.info(
                "gate_deterministic_transfer_domain",
                task_id=task_id,
                reason=transfer_request_reason,
                skipped_semantic_router=True,
                skipped_planner=True,
            )
            return {
                **gate_updates,
                **summary_updates,
                **transfer_updates,
                "tasks": {task_id: spec},
                "waves": [[task_id]],
                "current_wave_index": 0,
                "planner_output": None,
                "direct_path_triggered": True,
                "semantic_path_shape": "deterministic_transfer_domain",
                **_route_observability_updates(
                    owner="guardrail",
                    decision=transfer_request_reason,
                    target_domain="transfer",
                    mode="new",
                ),
            }
        if transfer_request_reason in {"batch_transfer_command", "account_aware_transfer_command"}:
            transfer_updates = {
                "preplanner_expected_transaction_executors": ["transfer"],
            }
            if (
                isinstance(query_session_snapshot, dict)
                and query_session_snapshot.get("session_active")
            ):
                await clear_query_session(redis_client, state.phone_number)
                transfer_updates.update(
                    _build_query_session_exit_updates(
                        state,
                        query_session_snapshot=query_session_snapshot,
                    )
                )
            logger.info(
                "gate_transfer_planner_handoff",
                reason=transfer_request_reason,
                skipped_semantic_router=True,
                target_domain="transfer",
            )
            return {
                **gate_updates,
                **summary_updates,
                **transfer_updates,
                **_route_observability_updates(
                    owner="guardrail",
                    decision=transfer_request_reason,
                    target_domain="transfer",
                    mode="new",
                ),
            }

    interrupt_kind = getattr(state.pending_interrupt, "kind", None)
    skip_semantic_router_for_interrupt = live_pending_interrupt and (
        interrupt_kind in {"confirmation", "auth"} or _is_numeric_input_interrupt_selection(state, message_text)
    )

    if skip_semantic_router_for_interrupt:
        logger.info(
            "gate_semantic_router_skipped_for_interrupt",
            kind=interrupt_kind,
            task_ids=getattr(state.pending_interrupt, "task_ids", None),
        )

    if (
        not skip_semantic_router_for_interrupt
        and not state.has_quote
        and callable(getattr(task_planner, "route_semantic_turn", None))
        and (live_pending_interrupt or _should_invoke_semantic_router(message_text))
    ):
        try:
            route_context = _build_semantic_router_context(
                turn_summary,
                state.preplanner_expected_transaction_executors,
                message_text=message_text,
            )
            try:
                route = await task_planner.route_semantic_turn(
                    state.phone_number,
                    message_text,
                    context=route_context,
                    path_label="direct_path",
                )
            except TypeError:
                route = await task_planner.route_semantic_turn(
                    state.phone_number,
                    message_text,
                    context=route_context,
                )
        except Exception as exc:
            logger.warning("gate_semantic_router_failed", error=str(exc))
            route = None

        if route is not None:
            canonical_decision = _semantic_route_decision(route)
            canonical_mode = _semantic_route_mode(route)
            requested_locale = getattr(route, "requested_language", None)
            if requested_locale:
                resolved_locale = LocaleManager.parse_locale_name(requested_locale)
                if resolved_locale is None:
                    logger.info("gate_semantic_router_locale_switch_invalid", requested_locale=requested_locale)
                else:
                    if redis_client:
                        resolved = await LocaleManager.set_locale(
                            state.phone_number,
                            resolved_locale,
                            source="user_command",
                        )
                        next_locale = resolved.value
                    else:
                        next_locale = resolved_locale.value
                    logger.info("gate_semantic_router_locale_switch", locale=next_locale)
                    return {
                        "direct_path_triggered": True,
                        "final_response": render_locale_switched(next_locale),
                        **_locale_update(state, next_locale),
                        **_route_observability_updates(
                            owner="semantic_router",
                            decision=canonical_decision or "direct_reply",
                            mode=canonical_mode,
                        ),
                    }

            expected_executors = [
                str(item)
                for item in (getattr(route, "expected_transaction_executors", None) or [])
                if str(item) in TRANSACTION_EXECUTORS
            ]
            updates: dict[str, Any] = {}
            if expected_executors:
                updates["preplanner_expected_transaction_executors"] = expected_executors

            if live_pending_interrupt:
                route = None
                canonical_decision = None
                canonical_mode = None

            if route is not None and canonical_decision == "cancel":
                locale, detected_locale_updates = await _effective_response_locale(
                    state=state,
                    redis_client=redis_client,
                    detected_language=getattr(route, "detected_language", None),
                )
                updates.update(detected_locale_updates)
                if has_cancelable_state(state):
                    text = cancelled_message(state, locale)
                    updates.update(await build_cancellation_reset_updates(state, redis_client))
                else:
                    text = clarify_message(state, locale)
                return {
                    **gate_updates,
                    **summary_updates,
                    "direct_path_triggered": True,
                    "final_response": text,
                    "semantic_path_shape": "semantic_router_direct",
                    **_route_observability_updates(
                        owner="semantic_router",
                        decision=canonical_decision,
                        mode=canonical_mode,
                    ),
                    **updates,
                }

            if route is not None and canonical_decision in {"direct_reply", "direct_context_answer"}:
                locale, detected_locale_updates = await _effective_response_locale(
                    state=state,
                    redis_client=redis_client,
                    detected_language=getattr(route, "detected_language", None),
                )
                updates.update(detected_locale_updates)
                if route.response_key:
                    if route.response_key == "conversational.out_of_scope":
                        text = format_out_of_scope_reply(locale, route.response)
                    elif route.response_key == "planner.cancelled":
                        if has_cancelable_state(state):
                            text = cancelled_message(state, locale)
                            updates.update(await build_cancellation_reset_updates(state, redis_client))
                        else:
                            text = clarify_message(state, locale)
                    else:
                        text = render_message(route.response_key, locale)
                else:
                    text = route.response or render_message("conversational.clarify", locale)

                had_active_query_session = bool(
                    isinstance(query_session_snapshot, dict) and query_session_snapshot.get("session_active")
                )
                had_pending_query_clarification = bool(
                    isinstance(query_session_snapshot, dict) and query_session_snapshot.get("pending_clarification")
                )
                if had_active_query_session:
                    await clear_query_session(redis_client, state.phone_number)
                    updates.update(
                        _build_query_session_exit_updates(
                            state,
                            query_session_snapshot=query_session_snapshot,
                        )
                    )
                    logger.info(
                        "gate_query_session_exited_on_direct_reply",
                        had_pending_clarification=had_pending_query_clarification,
                    )
                logger.info(
                    "gate_semantic_router_direct_response",
                    decision=canonical_decision,
                    response_key=route.response_key,
                    locale=locale,
                )
                return {
                    **gate_updates,
                    **summary_updates,
                    "direct_path_triggered": True,
                    "final_response": text,
                    "semantic_path_shape": "semantic_router_direct",
                    **_route_observability_updates(
                        owner="semantic_router",
                        decision=canonical_decision,
                        mode=canonical_mode,
                    ),
                    **updates,
                }

            route_to_domain: dict[
                str,
                Literal["query", "account", "support", "beneficiary", "transfer", "airtime", "data"],
            ] = {
                "domain_query": "query",
                "domain_account": "account",
                "domain_support": "support",
                "domain_beneficiary": "beneficiary",
                "domain_transfer": "transfer",
                "domain_airtime": "airtime",
                "domain_data": "data",
            }
            if route is not None and canonical_decision in route_to_domain:
                domain = route_to_domain[canonical_decision]
                mixed_executors = _obvious_mixed_transaction_executors(message_text)
                if domain in TRANSACTION_EXECUTORS and mixed_executors:
                    updates["preplanner_expected_transaction_executors"] = mixed_executors
                    logger.info(
                        "gate_semantic_router_mixed_veto",
                        decision=canonical_decision,
                        attempted_domain=domain,
                        expected_executors=mixed_executors,
                    )
                    return {
                        **gate_updates,
                        **summary_updates,
                        **_route_observability_updates(
                            owner="planner",
                            decision="planner_handoff",
                            mode=canonical_mode,
                        ),
                        **updates,
                    }
                if (
                    domain != "query"
                    and isinstance(query_session_snapshot, dict)
                    and query_session_snapshot.get("session_active")
                ):
                    await clear_query_session(redis_client, state.phone_number)
                    updates.update(
                        _build_query_session_exit_updates(
                            state,
                            query_session_snapshot=query_session_snapshot,
                        )
                    )
                task_id, spec = _build_direct_domain_task(
                    state=state,
                    domain=domain,
                    mode=canonical_mode,
                )
                logger.info(
                    "gate_semantic_router_domain_dispatch",
                    decision=canonical_decision,
                    domain=domain,
                    mode=canonical_mode,
                    task_id=task_id,
                )
                return {
                    **gate_updates,
                    **summary_updates,
                    "tasks": {task_id: spec},
                    "waves": [[task_id]],
                    "current_wave_index": 0,
                    "planner_output": None,
                    "direct_path_triggered": True,
                    "semantic_path_shape": "semantic_router_domain",
                    **_route_observability_updates(
                        owner="semantic_router",
                        decision=canonical_decision,
                        target_domain=domain,
                        mode=canonical_mode,
                    ),
                    **updates,
                }

            if updates:
                logger.info("gate_semantic_router_expected_executors", executors=expected_executors)
                return {
                    **gate_updates,
                    **summary_updates,
                    **_route_observability_updates(
                        owner="planner",
                        decision=canonical_decision or "planner_handoff",
                        mode=canonical_mode,
                    ),
                    **updates,
                }

    logger.info("gate_dispatch_to_planner", reason="planner_owned_or_unresolved_route")
    return {
        **gate_updates,
        **summary_updates,
        **_route_observability_updates(owner="planner", decision="planner_handoff"),
    }
