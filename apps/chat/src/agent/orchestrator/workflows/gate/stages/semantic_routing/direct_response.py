"""Direct response handler for the semantic router."""

from typing import Any

from apps.chat.src.agent.orchestrator.conversation.conversation_responder_modes import (
    SOCIAL_META_RENDER_PARAMS_CTX,
    SOCIAL_META_RESPONSE_KEY_CTX,
    SOCIAL_META_RESPONSE_KEYS,
    ConversationResponseMode,
    map_response_key_to_mode,
)
from apps.chat.src.agent.orchestrator.conversation.conversation_responder_validation import validate_and_fallback
from apps.chat.src.agent.orchestrator.guardrails.banking_ambiguity import (
    render_banking_coded_ambiguity_prompt,
)
from apps.chat.src.agent.orchestrator.guardrails.cancellation import (
    build_cancellation_reset_updates,
    cancelled_message,
    clarify_message,
    has_cancelable_state,
)
from apps.chat.src.agent.orchestrator.models.turn_directive import RouteResolution
from apps.chat.src.agent.orchestrator.presentation.conversational_style import (
    format_out_of_scope_reply,
)
from apps.chat.src.agent.orchestrator.workflows.gate.core.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.core.outcomes import direct_response, task_dispatch
from apps.chat.src.agent.orchestrator.workflows.gate.state.query_session_exit import (
    _build_query_session_exit_updates,
)
from apps.chat.src.agent.orchestrator.workflows.gate.utils.direct_tasks import (
    _build_direct_domain_task,
)
from banking.policy.service import resolve_available_conversational_suggestions
from banking.presentation.i18n.message_keys import MessageKey
from banking.presentation.i18n.renderer import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _validated_semantic_reply(
    ctx: GateContext,
    *,
    raw_response: str | None,
    mode: ConversationResponseMode,
    locale: str,
    extra_user_ctx: dict[str, object] | None = None,
) -> str:
    """Validate router-authored direct copy without paying for a second LLM."""
    return validate_and_fallback(
        raw_content=raw_response,
        mode=mode,
        text=ctx.message_text,
        user_ctx={
            **ctx.state_view.loaded_context_or_empty,
            "language": locale,
            **(extra_user_ctx or {}),
        },
        locale=locale,
        allowed_suggestions=resolve_available_conversational_suggestions(locale=locale),
    )


async def _handle_semantic_direct_response(
    ctx: GateContext,
    *,
    route: Any,
    updates: dict[str, Any],
    canonical_decision: str | None,
    canonical_mode: str | None,
) -> RouteResolution | None:
    if canonical_decision not in {"direct_reply", "direct_context_answer"}:
        return None

    locale = ctx.current_locale
    had_active_query_session = await ctx.has_active_query_session()
    has_live_query_focus = ctx.state_view.active_domain == "query" or ctx.state_view.has_session_for_domain("query")
    active_query_should_own_direct_answer = canonical_decision == "direct_reply" or (
        canonical_decision == "direct_context_answer" and has_live_query_focus
    )
    if had_active_query_session and active_query_should_own_direct_answer and route.response_key != "planner.cancelled":
        task_id, spec = _build_direct_domain_task(state_view=ctx.state_view, domain="query")
        logger.info(
            "gate_active_query_session_owns_direct_reply",
            semantic_decision=canonical_decision,
            response_key=route.response_key,
        )
        return task_dispatch(
            ctx,
            tasks={task_id: spec},
            waves=[[task_id]],
            owner="query_session",
            decision="query_followup_bypass",
            target_domain="query",
            mode="continuation",
            source="semantic_router",
            path_shape="query_followup_bypass",
            extra_updates={**(ctx.summary_updates or {}), **updates},
        )
    if (
        ctx.ambiguous_banking_domain is not None
        and not ctx.state_view.has_session_stack
        and not ctx.state_view.has_waves
        and not ctx.state_view.has_pending_interrupt
        and route.response_key in {"conversational.casual_chat", "conversational.out_of_scope"}
    ):
        logger.info(
            "gate_semantic_router_banking_coded_ambiguity_override",
            domain=ctx.ambiguous_banking_domain,
            response_key=route.response_key,
        )
        return direct_response(
            ctx,
            response=render_banking_coded_ambiguity_prompt(ctx.message_text, locale=locale),
            owner="guardrail",
            decision=f"banking_coded_ambiguity_{ctx.ambiguous_banking_domain}",
            source="semantic_router_veto",
            path_shape="banking_coded_ambiguity_clarify",
            extra_updates={**(ctx.summary_updates or {}), **updates},
        )
    if route.response_key:
        if route.response_key == "planner.cancelled":
            if has_cancelable_state(ctx.state):
                text = cancelled_message(ctx.state, locale)
                updates.update(await build_cancellation_reset_updates(ctx.state))
            else:
                text = clarify_message(ctx.state, locale)
        else:
            response_mode = map_response_key_to_mode(route.response_key)
            if response_mode is not None:
                extra_user_ctx: dict[str, object] = {}
                if route.response_key in SOCIAL_META_RESPONSE_KEYS:
                    extra_user_ctx = {
                        SOCIAL_META_RESPONSE_KEY_CTX: route.response_key,
                        SOCIAL_META_RENDER_PARAMS_CTX: {},
                    }
                text = _validated_semantic_reply(
                    ctx,
                    raw_response=route.response,
                    mode=response_mode,
                    locale=locale,
                    extra_user_ctx=extra_user_ctx,
                )
                # A router may supply only an empathy preface.  The redirect
                # remains mandatory for an out-of-scope turn, so retain that
                # bounded prose and append the localized safe next step.
                if route.response_key == "conversational.out_of_scope":
                    text = format_out_of_scope_reply(locale, text)
            elif route.response_key == "conversational.out_of_scope":
                text = format_out_of_scope_reply(locale, route.response)
            else:
                text = render_message(route.response_key, locale)
    else:
        text = route.response or ""
        if not text:
            fallback_key: MessageKey = (
                "conversational.out_of_scope" if canonical_decision == "direct_reply" else "conversational.clarify"
            )
            text = render_message(fallback_key, locale)

    if had_active_query_session:
        updates.update(
            _build_query_session_exit_updates(
                ctx.state,
            )
        )
        logger.info("gate_query_session_exited_on_direct_reply")
    logger.info(
        "gate_semantic_router_direct_response",
        decision=canonical_decision,
        response_key=route.response_key,
        locale=locale,
    )
    return direct_response(
        ctx,
        response=text,
        owner="semantic_router",
        decision=canonical_decision,
        mode=canonical_mode,
        source="semantic_router",
        path_shape="semantic_router_direct",
        extra_updates={**(ctx.summary_updates or {}), **updates},
    )
