import re
from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.cancellation import (
    cancel_match_kind,
    cancel_router_fallback_reason,
    is_obvious_cancel_message,
)
from apps.core.src.agent.orchestrator.nodes.interrupt.auth_resolve import _approve_auth_updates
from apps.core.src.agent.orchestrator.nodes.interrupt.confirmation_resolve import (
    _approve_confirmation_updates,
    _is_explicit_confirmation_approval_text,
    _resolve_deterministic_confirmation_repeat_route,
    _resolve_deterministic_confirmation_scope_update_route,
)
from apps.core.src.agent.orchestrator.nodes.interrupt.context import (
    _active_intent,
    _cancel_updates,
    _current_task_types,
    logger,
)
from apps.core.src.agent.orchestrator.nodes.interrupt.input_resolve import (
    _continue_flow_updates,
    _resolve_deterministic_input_selection_route,
    _resolve_deterministic_input_slot_route,
)
from apps.core.src.agent.orchestrator.nodes.interrupt.reprompt import (
    _reprompt_updates,
    _status_query_updates,
)
from apps.core.src.agent.orchestrator.nodes.interrupt.router import (
    _callback_flow_matches_interrupt,
    _handle_switch_intent_route,
    _is_same_flow_transactional_switch,
    _is_verified_pin_callback,
    _resolve_deterministic_status_query_route,
    _route_interrupt,
    _shortcut_miss_category,
)
from apps.core.src.agent.orchestrator.services.interrupt_shortcuts import (
    resolve_interrupt_shortcut_with_reason,
    resolve_shortcut_locale,
)

TRANSACTION_INTENTS = {"transfer", "airtime", "data"}
NON_TRANSACTION_SWITCH_INTENTS = {"query", "account", "faq", "support", "beneficiary"}
KNOWN_SWITCH_INTENTS = TRANSACTION_INTENTS | NON_TRANSACTION_SWITCH_INTENTS
INTERRUPT_REQUIRED_FIELDS_MAX_CHARS = 700
INTERRUPT_PROMPT_MAX_CHARS = 300
INTERRUPT_ACTIVE_TASK_STATE_MAX_CHARS = 700
INTERRUPT_REQUIRED_FIELDS_COMPACT_MAX_CHARS = 240
INTERRUPT_PROMPT_COMPACT_MAX_CHARS = 160
INTERRUPT_ACTIVE_TASK_STATE_COMPACT_MAX_CHARS = 320
_CONFIRMATION_UPDATE_VERB_RE = re.compile(
    r"\b(change|update|edit|instead|set|make(?:\s+it)?|replace|correct|meant|add|use)\b",
    re.IGNORECASE,
)
_CONFIRMATION_UPDATE_FIELD_RE = re.compile(
    r"\b(amount|bank|account|recipient|beneficiary|narration|memo|note|description)\b",
    re.IGNORECASE,
)
_CONFIRMATION_NOTE_FIELD_RE = re.compile(r"\b(narration|memo|note|description|reason|purpose)\b", re.IGNORECASE)
_CONFIRMATION_ITS_FOR_RE = re.compile(r"\b(?:it'?s|its|it is|this is)\s+for\b", re.IGNORECASE)
_CONFIRMATION_AMOUNT_RE = re.compile(
    r"(?:^|\s)(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKhH]?(?:\s|$)",
    re.IGNORECASE,
)
_INPUT_SIMPLE_AMOUNT_REPLY_RE = re.compile(r"^(?:₦?\d[\d,]*(?:\.\d+)?k?|all|everything|half|50%)$", re.IGNORECASE)
_CONFIRMATION_ACCOUNT_BANK_REPLY_RE = re.compile(r"[a-zA-Z].*\d[\d\s,.\-]{8,}|\d[\d\s,.\-]{8,}.*[a-zA-Z]")
_NON_TRANSFER_INTENT_HINT_RE = re.compile(
    r"\b(airtime|data|bundle|balance|statement|support|faq|ticket|complaint)\b",
    re.IGNORECASE,
)
_INPUT_RECIPIENT_REPLY_PREFIX_RE = re.compile(
    r"^(?:(?:it'?s|its|it is|this is)\s+)?(?:(?:to|for|send(?:\s+it)?\s+to)\s+)?(?P<recipient>.+?)$",
    re.IGNORECASE,
)
_INPUT_RECIPIENT_REPLY_BLOCK_RE = re.compile(
    r"\b(and|also|plus|then|while|cancel|stop|show|list|check|buy|help|support|faq|balance|statement|spend|spent|transaction|transactions|airtime|data|beneficiar(?:y|ies)|account(?:s)?|week|month|today|tomorrow|yesterday)\b",
    re.IGNORECASE,
)
_INPUT_RECIPIENT_REPLY_QUESTION_RE = re.compile(r"^(what|how|why|when|where|who|which)\b", re.IGNORECASE)
_INPUT_RECIPIENT_REPLY_META_RE = re.compile(
    r"^(hi|hello|hey|thanks|thank you|ok|okay|sure|yes|no)$",
    re.IGNORECASE,
)
_CONFIRMATION_COLLECTIVE_SCOPE_RE = re.compile(
    r"\b(both|all|everyone|everybody|all of them|for both)\b",
    re.IGNORECASE,
)
_CONFIRMATION_MULTI_CLAUSE_SPLIT_RE = re.compile(r"\s+(?:and|then)\s+|[;\n]+|,\s*", re.IGNORECASE)
_TRANSFER_CANCEL_SCHEDULE_RE = re.compile(
    r"\b(cancel|stop|delete|remove)\b[\w\s]{0,40}\b(schedule|scheduled|recurring|auto)\b",
    re.IGNORECASE,
)
_TRANSFER_RECURRING_RE = re.compile(r"\b(every|daily|weekly|monthly|recurring)\b", re.IGNORECASE)
_TRANSFER_SCHEDULE_RE = re.compile(
    r"\b(schedule|scheduled|tomorrow|today|later|next\s+\w+|on\s+\d{4}-\d{2}-\d{2})\b",
    re.IGNORECASE,
)
_SCOPED_CONFIRMATION_AMOUNT_RE = re.compile(
    r"(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKhH]?",
    re.IGNORECASE,
)







async def handle_pending_interrupt(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    """Process user input against the pending interrupt (if any)."""
    interrupt = state.pending_interrupt
    if not interrupt:
        return {}

    logger.info("handling_interrupt", kind=interrupt.kind, tasks=interrupt.task_ids)

    text = state.last_message_text or ""
    current_task_types = _current_task_types(state, interrupt.task_ids)
    active_type = _active_intent(current_task_types)
    task_planner = config["configurable"].get("task_planner")
    redis_client = config["configurable"].get("redis_client")
    services = config["configurable"].get("services") or {}

    if _is_verified_pin_callback(state) and interrupt.kind in {"confirmation", "auth"}:
        flow_matches, callback_flow_type = _callback_flow_matches_interrupt(state, current_task_types)
        if not flow_matches:
            logger.warning(
                "interrupt_callback_flow_mismatch",
                kind=interrupt.kind,
                callback_flow_type=callback_flow_type,
                active_types=sorted(current_task_types),
                tasks=interrupt.task_ids,
            )
            return _reprompt_updates(state, interrupt)
        if interrupt.kind == "confirmation":
            return _approve_confirmation_updates(state, interrupt)
        return _approve_auth_updates(state, interrupt)

    status_shortcut_route = _resolve_deterministic_status_query_route(
        state=state,
        interrupt=interrupt,
        text=text,
    )
    if status_shortcut_route is not None:
        return await _status_query_updates(
            state=state,
            interrupt=interrupt,
            route=status_shortcut_route,
            current_task_types=current_task_types,
            semantic_path_shape="interrupt_deterministic",
            task_planner=task_planner,
            text=text,
            active_type=active_type,
            services=services,
            redis_client=redis_client,
        )

    input_shortcut_route = _resolve_deterministic_input_selection_route(
        interrupt=interrupt,
        text=text,
    )
    if input_shortcut_route is not None:
        logger.info(
            "interrupt_input_shortcut_hit",
            kind=interrupt.kind,
            decision=input_shortcut_route.decision,
            reason=input_shortcut_route.reason,
        )
        return _continue_flow_updates(state, interrupt)

    input_slot_shortcut_route = _resolve_deterministic_input_slot_route(
        state=state,
        interrupt=interrupt,
        text=text,
    )
    if input_slot_shortcut_route is not None:
        logger.info(
            "interrupt_input_shortcut_hit",
            kind=interrupt.kind,
            decision=input_slot_shortcut_route.decision,
            reason=input_slot_shortcut_route.reason,
        )
        return _continue_flow_updates(state, interrupt)

    repeat_shortcut_route = _resolve_deterministic_confirmation_repeat_route(
        state=state,
        interrupt=interrupt,
        text=text,
    )
    if repeat_shortcut_route is not None:
        logger.info(
            "interrupt_shortcut_hit",
            kind=interrupt.kind,
            decision=repeat_shortcut_route.decision,
            reason=repeat_shortcut_route.reason,
            status_query_type=repeat_shortcut_route.status_query_type,
            locale=resolve_shortcut_locale((state.loaded_context or {}).get("language")).value
            if resolve_shortcut_locale((state.loaded_context or {}).get("language"))
            else None,
        )
        return _continue_flow_updates(state, interrupt)

    confirmation_scope_shortcut_route = _resolve_deterministic_confirmation_scope_update_route(
        state=state,
        interrupt=interrupt,
        text=text,
    )
    if confirmation_scope_shortcut_route is not None:
        logger.info(
            "interrupt_shortcut_hit",
            kind=interrupt.kind,
            decision=confirmation_scope_shortcut_route.decision,
            reason=confirmation_scope_shortcut_route.reason,
            status_query_type=confirmation_scope_shortcut_route.status_query_type,
        )
        return _continue_flow_updates(state, interrupt)

    deterministic_cancel_kind = cancel_match_kind(text) if is_obvious_cancel_message(text) else None
    if deterministic_cancel_kind is not None:
        logger.info(
            "interrupt_deterministic_cancel_hit",
            kind=interrupt.kind,
            match_kind=deterministic_cancel_kind,
        )
        return await _cancel_updates(state, interrupt, current_task_types, redis_client)

    if interrupt.kind == "auth":
        shortcut_locale = resolve_shortcut_locale((state.loaded_context or {}).get("language"))
        shortcut_route, miss_reason = resolve_interrupt_shortcut_with_reason(
            text=text,
            interrupt_kind=interrupt.kind,
            locale=shortcut_locale,
        )

        if shortcut_route is not None:
            logger.info(
                "interrupt_auth_shortcut_ignored",
                decision=shortcut_route.decision,
                locale=shortcut_locale.value if shortcut_locale else None,
            )
        else:
            logger.info(
                "interrupt_shortcut_miss",
                kind=interrupt.kind,
                locale=shortcut_locale.value if shortcut_locale else None,
                reason=miss_reason,
                miss_category=_shortcut_miss_category(miss_reason),
            )
            logger.info(
                "interrupt_cancel_router_fallback",
                kind=interrupt.kind,
                reason=cancel_router_fallback_reason(text),
            )

        route = await _route_interrupt(
            task_planner=task_planner,
            state=state,
            text=text,
            kind=interrupt.kind,
            task_ids=interrupt.task_ids,
            current_task_types=current_task_types,
            fields_by_task=interrupt.fields_by_task,
            prompt=interrupt.prompt,
        )

        if route.decision in {"cancel", "reject_flow"}:
            return await _cancel_updates(state, interrupt, current_task_types, redis_client)

        if route.decision == "switch_intent":
            if _is_same_flow_transactional_switch(route=route, interrupt=interrupt, active_type=active_type):
                logger.info(
                    "interrupt_same_flow_switch_shortcut",
                    kind=interrupt.kind,
                    active_type=active_type,
                    target_intent=route.target_intent,
                )
                return _continue_flow_updates(state, interrupt)
            return await _handle_switch_intent_route(
                state=state,
                interrupt=interrupt,
                route=route,
                task_planner=task_planner,
                text=text,
                active_type=active_type,
                current_task_types=current_task_types,
                services=services,
            )

        # Auth approval is callback-only for PIN. Non-PIN auth (e.g., OTP) can
        # still advance via explicit approve_flow from router classification.
        if route.decision == "approve_flow" and (interrupt.auth_method or "").lower() != "pin":
            return _approve_auth_updates(state, interrupt)

        # For PIN auth, free text cannot advance authorization.
        return _reprompt_updates(state, interrupt)

    shortcut_locale = resolve_shortcut_locale((state.loaded_context or {}).get("language"))
    shortcut_route, miss_reason = resolve_interrupt_shortcut_with_reason(
        text=text,
        interrupt_kind=interrupt.kind,
        locale=shortcut_locale,
    )

    if shortcut_route is not None:
        route = shortcut_route
        logger.info(
            "interrupt_shortcut_hit",
            kind=interrupt.kind,
            decision=route.decision,
            reason=route.reason,
            status_query_type=route.status_query_type,
            locale=shortcut_locale.value if shortcut_locale else None,
        )
    else:
        logger.info(
            "interrupt_shortcut_miss",
            kind=interrupt.kind,
            locale=shortcut_locale.value if shortcut_locale else None,
            reason=miss_reason,
            miss_category=_shortcut_miss_category(miss_reason),
        )
        logger.info(
            "interrupt_cancel_router_fallback",
            kind=interrupt.kind,
            reason=cancel_router_fallback_reason(text),
        )
        route = await _route_interrupt(
            task_planner=task_planner,
            state=state,
            text=text,
            kind=interrupt.kind,
            task_ids=interrupt.task_ids,
            current_task_types=current_task_types,
            fields_by_task=interrupt.fields_by_task,
            prompt=interrupt.prompt,
        )

    if route.decision == "status_query":
        return await _status_query_updates(
            state=state,
            interrupt=interrupt,
            route=route,
            current_task_types=current_task_types,
            semantic_path_shape="interrupt_router_only",
            task_planner=task_planner,
            text=text,
            active_type=active_type,
            services=services,
            redis_client=redis_client,
        )

    if route.decision == "cancel":
        return await _cancel_updates(state, interrupt, current_task_types, redis_client)

    if route.decision == "reject_flow":
        return await _cancel_updates(state, interrupt, current_task_types, redis_client)

    if route.decision == "approve_flow":
        if interrupt.kind == "confirmation":
            if not _is_explicit_confirmation_approval_text(state, text):
                logger.info(
                    "confirmation_approve_blocked_non_explicit_text",
                    tasks=interrupt.task_ids,
                )
                return _continue_flow_updates(state, interrupt)
            return _approve_confirmation_updates(state, interrupt)
        if interrupt.kind == "auth":
            return _approve_auth_updates(state, interrupt)
        # Input interrupts cannot be "approved"; keep flow deterministic.
        return _reprompt_updates(state, interrupt)

    if route.decision == "continue_flow":
        return _continue_flow_updates(state, interrupt)

    if route.decision == "switch_intent":
        if _is_same_flow_transactional_switch(route=route, interrupt=interrupt, active_type=active_type):
            logger.info(
                "interrupt_same_flow_switch_shortcut",
                kind=interrupt.kind,
                active_type=active_type,
                target_intent=route.target_intent,
            )
            return _continue_flow_updates(state, interrupt)
        return await _handle_switch_intent_route(
            state=state,
            interrupt=interrupt,
            route=route,
            task_planner=task_planner,
            text=text,
            active_type=active_type,
            current_task_types=current_task_types,
            services=services,
        )

    # "unclear" or any unrecognized decision stays non-destructive.
    return _reprompt_updates(state, interrupt)
