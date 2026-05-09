"""Planner no-task and cancellation response flow helpers."""

from typing import Any, cast

from apps.chat.src.agent.orchestrator.banking_ambiguity import (
    classify_banking_coded_ambiguity,
    render_banking_coded_ambiguity_prompt,
)
from apps.chat.src.agent.orchestrator.conversational_style import format_out_of_scope_reply
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.nodes.cancellation import (
    build_cancellation_reset_updates,
    cancelled_message,
    clarify_message,
    has_cancelable_state,
)
from apps.chat.src.agent.orchestrator.nodes.interrupt.reprompt import _reprompt_updates
from apps.chat.src.agent.orchestrator.nodes.planner.policy import (
    _build_locale_update,
    _build_policy_aware_greeting,
    _detected_locale_value,
)
from shared.i18n import LocaleManager, MessageKey, render_message, render_safe_capability_fallback, render_text
from shared.services.conversation_responder import (
    is_banking_refusal_reply,
    is_contextual_casual_followup_turn,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _log_unexpected_turn_route(
    *,
    state: OrchestratorState,
    planner_output: Any,
    selected_route: str,
    route_reason: str,
    policy_blocked: bool,
    fallback_path: str | None,
) -> None:
    logger.info(
        "unexpected_turn_route_breadcrumb",
        user_turn_kind=str(getattr(planner_output, "primary_intent", "unknown") or "unknown"),
        active_session_present=bool(state.session_stack),
        selected_route=selected_route,
        route_reason=route_reason,
        policy_blocked=policy_blocked,
        fallback_path=fallback_path,
    )


async def _build_non_task_response(
    *,
    state: OrchestratorState,
    planner_output: Any,
    text: str,
    redis_client: Any | None,
    active_intent: str | None,
    current_locale: str,
    locale_updates: dict[str, Any],
    context_read_updates: dict[str, Any],
    conversation_responder: Any | None = None,
) -> dict[str, Any] | None:
    detected_locale = _detected_locale_value(planner_output)

    async def _resolved_locale_with_precedence() -> tuple[str, dict[str, Any]]:
        if detected_locale is None or detected_locale == current_locale:
            return current_locale, locale_updates
        if redis_client and await LocaleManager.is_explicit_locale(state.phone_number):
            return current_locale, locale_updates
        return detected_locale, _build_locale_update(state, detected_locale)

    def _localized_planner_response(raw_response: str | None, locale: str) -> str:
        if not raw_response:
            return ""
        return cast(str, render_text(raw_response, locale))

    async def _build_bounded_conversational_reply(locale: str) -> str | None:
        if conversation_responder is None:
            return None
        try:
            return await conversation_responder.generate_reply(
                state.phone_number,
                text,
                {
                    **(state.loaded_context or {}),
                    "language": locale,
                },
                intent="non_banking_conversational",
            )
        except Exception as exc:
            logger.warning("conversation_responder_failed", error=str(exc))
            return None

    if getattr(planner_output, "is_cancellation", False) or planner_output.primary_intent == "cancel":
        logger.info("planner_cancellation_detected", intent=planner_output.primary_intent)
        cancel_locale = detected_locale or current_locale
        cancel_locale_updates = locale_updates if cancel_locale == current_locale else _build_locale_update(
            state, cancel_locale
        )
        if not has_cancelable_state(state):
            return {
                "final_response": clarify_message(state, cancel_locale),
                **cancel_locale_updates,
                **context_read_updates,
            }
        cancel_message = cancelled_message(state, cancel_locale)
        reset_updates = await build_cancellation_reset_updates(state, redis_client)
        return {
            **reset_updates,
            "final_response": cancel_message,
            **cancel_locale_updates,
        }

    if planner_output and planner_output.tasks:
        return None

    if planner_output and planner_output.primary_intent == "conversational":
        conversational_locale, conversational_locale_updates = await _resolved_locale_with_precedence()
        ambiguous_banking_domain = classify_banking_coded_ambiguity(text)
        if (
            ambiguous_banking_domain is not None
            and state.pending_interrupt is None
            and not state.session_stack
            and not state.waves
        ):
            _log_unexpected_turn_route(
                state=state,
                planner_output=planner_output,
                selected_route="banking_ambiguity_clarify",
                route_reason=f"banking_coded_ambiguity:{ambiguous_banking_domain}",
                policy_blocked=False,
                fallback_path="planner_non_task",
            )
            return {
                "final_response": render_banking_coded_ambiguity_prompt(text, locale=conversational_locale),
                **conversational_locale_updates,
                **context_read_updates,
            }
        contextual_casual_followup = (
            state.pending_interrupt is None
            and not state.session_stack
            and not state.waves
            and is_contextual_casual_followup_turn(
                text,
                (state.loaded_context or {}).get("history") if isinstance(state.loaded_context, dict) else None,
            )
        )
        if contextual_casual_followup:
            responder_reply = await _build_bounded_conversational_reply(conversational_locale)
            if responder_reply:
                _log_unexpected_turn_route(
                    state=state,
                    planner_output=planner_output,
                    selected_route="conversation_responder",
                    route_reason="contextual_casual_followup",
                    policy_blocked=False,
                    fallback_path="planner_non_task",
                )
                return {
                    "final_response": responder_reply,
                    **conversational_locale_updates,
                    **context_read_updates,
                }
        response_key = planner_output.response_key
        if response_key == "conversational.casual_chat":
            responder_reply = await _build_bounded_conversational_reply(conversational_locale)
            _log_unexpected_turn_route(
                state=state,
                planner_output=planner_output,
                selected_route="conversation_responder",
                route_reason="conversational_casual_chat",
                policy_blocked=False,
                fallback_path="planner_non_task",
            )
            return {
                "final_response": responder_reply
                or render_message("conversational.out_of_scope", conversational_locale),
                **conversational_locale_updates,
                **context_read_updates,
            }
        if response_key == "conversational.out_of_scope":
            responder_reply = None
            if not planner_output.response or is_banking_refusal_reply(
                planner_output.response,
                locale=conversational_locale,
            ):
                responder_reply = await _build_bounded_conversational_reply(conversational_locale)
            empathy_source = (
                _localized_planner_response(planner_output.response, conversational_locale)
                if planner_output.response
                else None
            )
            _log_unexpected_turn_route(
                state=state,
                planner_output=planner_output,
                selected_route="out_of_scope",
                route_reason="conversational_out_of_scope",
                policy_blocked=True,
                fallback_path="planner_non_task",
            )
            return {
                "final_response": responder_reply or format_out_of_scope_reply(conversational_locale, empathy_source),
                **conversational_locale_updates,
                **context_read_updates,
            }

        if planner_output.response:
            logger.info("planner_direct_response_used", locale=conversational_locale)
            _log_unexpected_turn_route(
                state=state,
                planner_output=planner_output,
                selected_route="direct_response",
                route_reason="planner_conversational_response",
                policy_blocked=False,
                fallback_path="planner_non_task",
            )
            return {
                "final_response": _localized_planner_response(planner_output.response, conversational_locale),
                **conversational_locale_updates,
                **context_read_updates,
            }

        if response_key:
            logger.info("planner_response_key_used", key=response_key, locale=conversational_locale)
            if response_key == "conversational.greeting":
                return {
                    "final_response": _build_policy_aware_greeting(conversational_locale),
                    **conversational_locale_updates,
                    **context_read_updates,
                }
            _log_unexpected_turn_route(
                state=state,
                planner_output=planner_output,
                selected_route="response_key_render",
                route_reason=f"response_key:{response_key}",
                policy_blocked=response_key == "conversational.out_of_scope",
                fallback_path="planner_non_task",
            )
            return {
                "final_response": render_message(response_key, conversational_locale),
                **conversational_locale_updates,
                **context_read_updates,
            }

        logger.info(
            "planner_response_key_missing_and_no_response",
            intent=planner_output.primary_intent,
            detected_language=getattr(planner_output, "detected_language", None),
        )
        responder_reply = await _build_bounded_conversational_reply(conversational_locale)
        if responder_reply:
            _log_unexpected_turn_route(
                state=state,
                planner_output=planner_output,
                selected_route="conversation_responder",
                route_reason="missing_conversational_response_key",
                policy_blocked=False,
                fallback_path="planner_non_task",
            )
            return {
                "final_response": responder_reply,
                **conversational_locale_updates,
                **context_read_updates,
            }
        fallback_key: MessageKey = "conversational.out_of_scope"
        logger.info("conversational_fallback_deterministic_used", key=fallback_key, locale=conversational_locale)
        _log_unexpected_turn_route(
            state=state,
            planner_output=planner_output,
            selected_route="out_of_scope",
            route_reason="missing_conversational_response_key",
            policy_blocked=False,
            fallback_path="planner_non_task",
        )
        return {
            "final_response": render_message(fallback_key, conversational_locale),
            **conversational_locale_updates,
                **context_read_updates,
            }

    if state.waves and planner_output and planner_output.primary_intent != "conversational":
        if planner_output.primary_intent != active_intent:
            logger.info("planner_switch_empty_tasks", old=active_intent, new=planner_output.primary_intent)
            return {
                "waves": [],
                "final_response": _localized_planner_response(planner_output.response, current_locale),
                **locale_updates,
                **context_read_updates,
            }

    if planner_output and planner_output.response:
        _log_unexpected_turn_route(
            state=state,
            planner_output=planner_output,
            selected_route="direct_response",
            route_reason="planner_non_conversational_response",
            policy_blocked=False,
            fallback_path="planner_non_task",
        )
        return {
            "final_response": _localized_planner_response(planner_output.response, current_locale),
            **locale_updates,
            **context_read_updates,
        }
    if not state.session_stack and state.pending_interrupt is None:
        responder_reply = await _build_bounded_conversational_reply(current_locale)
        if responder_reply:
            _log_unexpected_turn_route(
                state=state,
                planner_output=planner_output,
                selected_route="conversation_responder",
                route_reason="no_tasks_no_response",
                policy_blocked=False,
                fallback_path="planner_non_task",
            )
            return {
                "final_response": responder_reply,
                **locale_updates,
                **context_read_updates,
            }
    if state.pending_interrupt is not None:
        _log_unexpected_turn_route(
            state=state,
            planner_output=planner_output,
            selected_route="interrupt_reprompt",
            route_reason="no_tasks_no_response_pending_interrupt",
            policy_blocked=False,
            fallback_path="planner_non_task",
        )
        return {
            **_reprompt_updates(state, state.pending_interrupt),
            **context_read_updates,
        }
    _log_unexpected_turn_route(
        state=state,
        planner_output=planner_output,
        selected_route="capability_fallback",
        route_reason="no_tasks_no_response",
        policy_blocked=False,
        fallback_path="planner_non_task",
    )
    return {
        "final_response": render_safe_capability_fallback(current_locale),
        **locale_updates,
        **context_read_updates,
    }


__all__ = ["_build_non_task_response", "_log_unexpected_turn_route"]
