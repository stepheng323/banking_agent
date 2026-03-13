"""Session Gate Node (Fast Path).

Determines whether to skip the Planner LLM based on active session context.
Implements deterministic routing for active sessions and query continuation.
"""

import re
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.conversational_style import format_out_of_scope_reply
from apps.core.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.cancellation import (
    build_cancellation_reset_updates,
    cancelled_message,
    clarify_message,
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
from shared.utils.logging import get_logger

logger = get_logger(__name__)

TRANSACTION_EXECUTORS = {"transfer", "airtime", "data"}
TURN_ROUTER_MAX_WORDS = 8
TURN_ROUTER_MAX_CHARS = 64
TURN_ROUTER_MULTI_CLAUSE_MARKERS = (" and ", " & ", " then ", ",")
TURN_ROUTER_META_PATTERNS = (
    r"\b(hi|hello|hey|how far|good (morning|afternoon|evening))\b",
    r"\b(who are you|what can you do|help me|can you help)\b",
    r"\b(loan|borrow|hungry|starving|food|book (a|my)|flight|hotel)\b",
    r"\b(thank you|thanks|sorry|get out|leave me)\b",
    r"\b(cancel|abort|stop|nevermind|never mind)\b",
)
TURN_ROUTER_TRANSACTION_HINT_PATTERNS = (
    r"\b(send|transfer|buy|airtime|data|bundle|pay|fund|withdraw)\b",
)
TURN_ROUTER_QUESTION_STARTERS = {
    "can",
    "do",
    "does",
    "did",
    "is",
    "are",
    "was",
    "were",
    "what",
    "which",
    "who",
    "how",
    "where",
    "when",
    "why",
    "any",
    "more",
    "still",
}
TURN_ROUTER_IMPERATIVE_ACTION_PREFIXES = (
    "show ",
    "list ",
    "set ",
    "unlink ",
    "link ",
    "delete ",
    "remove ",
)
TURN_ROUTER_CONTEXT_HINT_PATTERNS = (
    r"\b(bank|account|acct|beneficiary|saved|debit|credit|transactions?|default|mandate|ready)\b",
)
ACCOUNT_BALANCE_REQUEST_PATTERNS = (
    r"\bbalance\b",
    r"\baccount\s+balance\b",
    r"\bcheck\s+my\s+balance\b",
    r"\bwhat(?:'s| is)\s+my\s+balance\b",
    r"\bhow\s+much\s+do\s+i\s+have\b",
    r"\bhow\s+much\s+is\s+in\s+my\s+account\b",
)
BALANCE_FASTPATH_TRANSACTION_HINT_PATTERNS = (
    r"\b(send|transfer|pay|buy|airtime|data|bundle|fund|withdraw)\b",
)
BALANCE_FASTPATH_CANCEL_PREFIX_RE = re.compile(
    r"^(?:cancel|abort|stop|nevermind|never\s+mind)(?:\s+(?:and|then))?\s+",
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


def _next_fast_query_task_id(existing_tasks: dict[str, TaskSpec]) -> str:
    idx = 1
    task_id = "fast_query_resume"
    while task_id in existing_tasks:
        idx += 1
        task_id = f"fast_query_resume_{idx}"
    return task_id


def _next_fast_account_task_id(existing_tasks: dict[str, TaskSpec]) -> str:
    idx = 1
    task_id = "fast_account_balance"
    while task_id in existing_tasks:
        idx += 1
        task_id = f"fast_account_balance_{idx}"
    return task_id


def _locale_update(state: OrchestratorState, locale: str) -> dict[str, Any]:
    loaded_context = dict(state.loaded_context or {})
    loaded_context["language"] = locale
    loaded_context["detected_language"] = locale
    return {"loaded_context": loaded_context}


def _should_invoke_turn_router(message_text: str) -> bool:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower())
    if not normalized:
        return False
    if _is_account_balance_request(normalized):
        return False
    has_multi_clause = any(marker in normalized for marker in TURN_ROUTER_MULTI_CLAUSE_MARKERS)
    has_money_move = bool(re.search(r"\b(send|transfer|pay|fund)\b", normalized))
    has_airtime_or_data = bool(re.search(r"\b(buy|airtime|data|bundle)\b", normalized))
    looks_mixed_transaction = has_multi_clause and has_money_move and has_airtime_or_data
    if looks_mixed_transaction:
        return True
    if any(char.isdigit() for char in normalized):
        return False
    if any(marker in normalized for marker in TURN_ROUTER_MULTI_CLAUSE_MARKERS):
        return False
    if len(normalized) > TURN_ROUTER_MAX_CHARS or len(normalized.split()) > TURN_ROUTER_MAX_WORDS:
        return False
    if any(re.search(pattern, normalized) for pattern in TURN_ROUTER_TRANSACTION_HINT_PATTERNS):
        return False
    if normalized.startswith(TURN_ROUTER_IMPERATIVE_ACTION_PREFIXES):
        return False
    if normalized.endswith("?"):
        return True
    first_word = normalized.split()[0] if normalized else ""
    if first_word in TURN_ROUTER_QUESTION_STARTERS:
        return True
    if any(re.search(pattern, normalized) for pattern in TURN_ROUTER_CONTEXT_HINT_PATTERNS):
        return True
    return any(re.search(pattern, normalized) for pattern in TURN_ROUTER_META_PATTERNS)


def _build_turn_router_context(summary: TurnContextSummary, expected_executors: list[str]) -> str:
    return build_router_context_from_summary(
        summary,
        expected_executors=expected_executors,
    )


def _is_account_balance_request(message_text: str) -> bool:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower())
    if not normalized:
        return False
    candidate = BALANCE_FASTPATH_CANCEL_PREFIX_RE.sub("", normalized)
    if any(re.search(pattern, candidate) for pattern in BALANCE_FASTPATH_TRANSACTION_HINT_PATTERNS):
        return False
    return any(re.search(pattern, candidate) for pattern in ACCOUNT_BALANCE_REQUEST_PATTERNS)


def _has_explicit_cancel(message_text: str) -> bool:
    normalized = re.sub(r"\s+", " ", message_text.strip().lower())
    if not normalized:
        return False
    return any(re.search(pattern, normalized) for pattern in EXPLICIT_CANCEL_PATTERNS)


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
    if any(marker in normalized for marker in TURN_ROUTER_MULTI_CLAUSE_MARKERS):
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


def _next_fast_beneficiary_task_id(existing_tasks: dict[str, TaskSpec]) -> str:
    idx = 1
    task_id = "fast_beneficiary_save"
    while task_id in existing_tasks:
        idx += 1
        task_id = f"fast_beneficiary_save_{idx}"
    return task_id


async def session_gate_fastpath(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    """
    Fast Path Gate.

    1. Check for Active Sessions (Input Interrupt).
    2. Check for Query Continuation.
    3. Fallback to Planner (LLM-first for conversational/meta routing).
    """

    task_planner = config["configurable"].get("task_planner")
    redis_client = config["configurable"].get("redis_client")
    session = state.session_stack[-1] if state.session_stack else None

    logger.info(
        "gate_entry", session_domain=session.domain if session else None, interrupt=state.pending_interrupt is not None
    )

    message_text = (state.last_message_text or "").strip()

    if is_explicit_cancel_message(message_text):
        if has_cancelable_state(state):
            cleanup_updates = await build_cancellation_reset_updates(state, redis_client)
            return {
                **cleanup_updates,
                "fast_path_triggered": True,
                "final_response": cancelled_message(state),
            }
        return {
            "fast_path_triggered": True,
            "final_response": clarify_message(state),
        }

    if not state.pending_interrupt:
        explicit_locale = LocaleManager.parse_explicit_switch_command(message_text)
        if explicit_locale:
            if redis_client:
                resolved = await LocaleManager.set_locale(state.phone_number, explicit_locale, source="user_command")
                next_locale = resolved.value
            else:
                next_locale = explicit_locale.value
            logger.info("gate_locale_switch_fastpath", locale=next_locale)
            return {
                "fast_path_triggered": True,
                "final_response": render_locale_switched(next_locale),
                **_locale_update(state, next_locale),
            }

    if not state.pending_interrupt and redis_client:
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

            locale = LocaleManager.normalize((state.loaded_context or {}).get("language")).value
            decision = _resolve_beneficiary_suggestion_reply(
                message_text,
                locale=locale,
                suggestion_payload=suggestion_payload,
            )
            logger.info(
                "beneficiary_suggestion_gate_decision",
                decision=decision.action,
                reason=decision.reason,
                locale=locale,
                alias_present=bool(decision.alias),
            )
            if decision.action in {"save_default", "save_alias"}:
                task_id = _next_fast_beneficiary_task_id(state.tasks)
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
                    "tasks": {task_id: spec},
                    "waves": [[task_id]],
                    "current_wave_index": 0,
                    "planner_output": None,
                    "pending_interrupt": None,
                    "fast_path_triggered": True,
                }

            try:
                await redis_client.delete(suggestion_key)
            except Exception as exc:
                logger.warning("beneficiary_suggestion_dismiss_delete_failed", error=str(exc))
            else:
                logger.info("beneficiary_suggestion_dismissed", reason=decision.reason)

    if not state.pending_interrupt and _is_account_balance_request(message_text):
        cleanup_updates: dict[str, Any] = {}
        if _has_explicit_cancel(message_text):
            cleanup_updates = await build_cancellation_reset_updates(state, redis_client)

        task_id = _next_fast_account_task_id(state.tasks)
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
        logger.info("gate_fast_account_balance", task_id=task_id, with_cleanup=bool(cleanup_updates))
        return {
            **cleanup_updates,
            "tasks": {task_id: spec},
            "waves": [[task_id]],
            "current_wave_index": 0,
            "planner_output": None,
            "pending_interrupt": None,
            "fast_path_triggered": True,
        }

    if not state.pending_interrupt and session:
        message_lowered = message_text.lower()
        logger.info("gate_tier0_check", domain=session.domain, input_fragment=message_lowered[:20])

        # --- 3. Fast Query Resume ---
        if session.domain == "query":
            fast_keywords = {
                "more",
                "next",
                "back",
                "previous",
                "prev",
                "show",
                "filter",
                "sort",
                "details",
                "first",
                "last",
                "latest",
                "oldest",
                "drill",
                "expand",
            }

            first_word = message_lowered.split()[0] if message_lowered else ""
            is_fast_match = first_word in fast_keywords or "page" in message_lowered or "only" in message_lowered

            if is_fast_match:
                logger.info("fast_path_query_match", phrase=first_word)

                task_id = _next_fast_query_task_id(state.tasks)
                spec = TaskSpec(
                    id=task_id,
                    type="query",
                    stage=TaskStage.DRAFT,
                    payload={
                        "message": state.last_message_text,
                        "is_fast_path": True,
                    },
                )

                return {
                    "tasks": {task_id: spec},
                    "waves": [[task_id]],
                    "current_wave_index": 0,
                    "planner_output": None,
                    "fast_path_triggered": True,
                }

    # --- PIN callback with no active session (checkpoint was cleaned) ---
    if state.pin_verified and not state.pending_interrupt:
        locale = LocaleManager.normalize((state.loaded_context or {}).get("language")).value
        logger.warning("gate_pin_verified_no_session", reason="checkpoint_cleaned")
        return {
            "fast_path_triggered": True,
            "final_response": render_message(
                "orchestrator.session.expired_pin",
                locale,
                fallback_en="Your transaction session has expired. Please start a new transaction.",
            ),
        }

    summary_path_label = (
        "interrupt_path"
        if state.pending_interrupt
        else (
            "fast_path"
            if (
                not state.has_quote
                and callable(getattr(task_planner, "route_turn", None))
                and _should_invoke_turn_router(message_text)
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

    if (
        not state.pending_interrupt
        and not state.has_quote
        and callable(getattr(task_planner, "route_turn", None))
        and _should_invoke_turn_router(message_text)
    ):
        try:
            route_context = _build_turn_router_context(turn_summary, state.preplanner_expected_transaction_executors)
            try:
                route = await task_planner.route_turn(
                    state.phone_number,
                    message_text,
                    context=route_context,
                    path_label="fast_path",
                )
            except TypeError:
                route = await task_planner.route_turn(
                    state.phone_number,
                    message_text,
                    context=route_context,
                )
        except Exception as exc:
            logger.warning("gate_turn_router_failed", error=str(exc))
            route = None

        if route is not None:
            expected_executors = [
                str(item)
                for item in (getattr(route, "expected_transaction_executors", None) or [])
                if str(item) in TRANSACTION_EXECUTORS
            ]
            updates: dict[str, Any] = {}
            if expected_executors:
                updates["preplanner_expected_transaction_executors"] = expected_executors

            if route.decision in {"respond_directly", "direct_context_answer"}:
                locale = LocaleManager.normalize((state.loaded_context or {}).get("language")).value
                if route.detected_language:
                    locale = LocaleManager.from_detection(route.detected_language).value
                    updates.update(_locale_update(state, locale))
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
                logger.info(
                    "gate_turn_router_direct_response",
                    decision=route.decision,
                    response_key=route.response_key,
                    locale=locale,
                )
                return {
                    **summary_updates,
                    "fast_path_triggered": True,
                    "final_response": text,
                    "semantic_path_shape": "turn_router_only",
                    **updates,
                }

            if route.decision == "query_continuation":
                if _is_account_balance_request(message_text):
                    logger.info("gate_turn_router_query_continuation_blocked_account_request", message=message_text)
                    if updates:
                        logger.info("gate_turn_router_expected_executors", executors=expected_executors)
                        return updates
                    logger.info("gate_fallback_to_planner", reason="query_continuation_blocked_account_request")
                    return {}
                task_id = _next_fast_query_task_id(state.tasks)
                spec = TaskSpec(
                    id=task_id,
                    type="query",
                    stage=TaskStage.DRAFT,
                    payload={
                        "message": state.last_message_text,
                        "is_fast_path": True,
                    },
                )
                logger.info("gate_turn_router_query_continuation", task_id=task_id)
                return {
                    **summary_updates,
                    "tasks": {task_id: spec},
                    "waves": [[task_id]],
                    "current_wave_index": 0,
                    "planner_output": None,
                    "fast_path_triggered": True,
                    "semantic_path_shape": "turn_router_only",
                    **updates,
                }

            if updates:
                logger.info("gate_turn_router_expected_executors", executors=expected_executors)
                return {
                    **summary_updates,
                    **updates,
                }

    logger.info("gate_fallback_to_planner", reason="no_fast_path_match")
    return summary_updates  # Fallback to planner logic
