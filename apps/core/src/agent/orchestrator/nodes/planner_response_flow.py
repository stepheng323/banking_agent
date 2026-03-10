"""Planner no-task and cancellation response flow helpers."""

from typing import Any, cast

from apps.core.src.agent.orchestrator.conversational_style import format_out_of_scope_reply
from apps.core.src.agent.orchestrator.meta_reply import generate_meta_reply
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.cancellation import (
    build_cancellation_reset_updates,
    cancelled_message,
    clarify_message,
    has_cancelable_state,
)
from apps.core.src.agent.orchestrator.nodes.planner_policy import (
    _build_locale_update,
    _build_policy_aware_greeting,
    _detected_locale_value,
    _meta_intent_from_response_key,
)
from shared.i18n import MessageKey, render_message, render_safe_capability_fallback, render_text
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def _build_non_task_response(
    *,
    state: OrchestratorState,
    planner_output: Any,
    text: str,
    task_planner: Any,
    redis_client: Any | None,
    active_intent: str | None,
    current_locale: str,
    locale_updates: dict[str, Any],
    fastpath_context_updates: dict[str, Any],
) -> dict[str, Any] | None:
    detected_locale = _detected_locale_value(planner_output)

    def _localized_planner_response(raw_response: str | None) -> str:
        if not raw_response:
            return ""
        return cast(str, render_text(raw_response, current_locale))

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
                **fastpath_context_updates,
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
        conversational_locale = detected_locale or current_locale
        conversational_locale_updates = (
            locale_updates
            if conversational_locale == current_locale
            else _build_locale_update(state, conversational_locale)
        )
        response_key = planner_output.response_key
        if response_key == "conversational.out_of_scope":
            empathy_source = _localized_planner_response(planner_output.response) if planner_output.response else None
            meta_intent = _meta_intent_from_response_key(response_key)
            llm = getattr(task_planner, "planner_llm", None)
            if meta_intent and llm is not None and hasattr(llm, "with_structured_output"):
                meta_message, handoff = await generate_meta_reply(
                    llm,
                    user_message=text,
                    user_language_hint=conversational_locale,
                    meta_intent=meta_intent,
                    redis_client=redis_client,
                )
                if handoff == "meta" and meta_message:
                    logger.info(
                        "planner_meta_reply_used",
                        response_key=response_key,
                        locale=conversational_locale,
                        intent=meta_intent.value,
                    )
                    empathy_source = meta_message
                else:
                    logger.info(
                        "planner_meta_reply_fallback",
                        response_key=response_key,
                        locale=conversational_locale,
                        handoff=handoff,
                    )
            return {
                "final_response": format_out_of_scope_reply(conversational_locale, empathy_source),
                **conversational_locale_updates,
                **fastpath_context_updates,
            }

        if planner_output.response:
            logger.info("planner_direct_response_used", locale=conversational_locale)
            return {
                "final_response": _localized_planner_response(planner_output.response),
                **conversational_locale_updates,
                **fastpath_context_updates,
            }

        if response_key:
            logger.info("planner_response_key_used", key=response_key, locale=conversational_locale)
            if response_key == "conversational.greeting":
                return {
                    "final_response": _build_policy_aware_greeting(conversational_locale),
                    **conversational_locale_updates,
                    **fastpath_context_updates,
                }

            meta_intent = _meta_intent_from_response_key(response_key)
            llm = getattr(task_planner, "planner_llm", None)
            if meta_intent and llm is not None and hasattr(llm, "with_structured_output"):
                meta_message, handoff = await generate_meta_reply(
                    llm,
                    user_message=text,
                    user_language_hint=conversational_locale,
                    meta_intent=meta_intent,
                    redis_client=redis_client,
                )
                if handoff == "meta" and meta_message:
                    logger.info(
                        "meta_query_route_hit",
                        source="response_key",
                        meta_kind=meta_intent.value,
                    )
                    logger.info(
                        "planner_meta_reply_used",
                        response_key=response_key,
                        locale=conversational_locale,
                        intent=meta_intent.value,
                    )
                    return {
                        "final_response": meta_message,
                        **conversational_locale_updates,
                        **fastpath_context_updates,
                    }
                logger.info(
                    "planner_meta_reply_fallback",
                    response_key=response_key,
                    locale=conversational_locale,
                    handoff=handoff,
                )
            return {
                "final_response": render_message(response_key, conversational_locale),
                **conversational_locale_updates,
                **fastpath_context_updates,
            }

        logger.info(
            "planner_response_key_missing_and_no_response",
            intent=planner_output.primary_intent,
            detected_language=getattr(planner_output, "detected_language", None),
        )
        fallback_key: MessageKey = "conversational.clarify"
        logger.info("conversational_fallback_deterministic_used", key=fallback_key, locale=conversational_locale)
        return {
            "final_response": render_message(fallback_key, conversational_locale),
            **conversational_locale_updates,
            **fastpath_context_updates,
        }

    if state.waves and planner_output and planner_output.primary_intent != "conversational":
        if planner_output.primary_intent != active_intent:
            logger.info("planner_switch_empty_tasks", old=active_intent, new=planner_output.primary_intent)
            return {
                "waves": [],
                "final_response": _localized_planner_response(planner_output.response),
                **locale_updates,
                **fastpath_context_updates,
            }

    if planner_output and planner_output.response:
        return {
            "final_response": _localized_planner_response(planner_output.response),
            **locale_updates,
            **fastpath_context_updates,
        }
    return {
        "final_response": render_safe_capability_fallback(current_locale),
        **locale_updates,
        **fastpath_context_updates,
    }


__all__ = ["_build_non_task_response"]
