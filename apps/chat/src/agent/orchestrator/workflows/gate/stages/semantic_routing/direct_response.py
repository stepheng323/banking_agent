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
from shared.types.prompt_boundary import is_melkor_eligible
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
    response_key = route.response_key
    raw_response = route.response
    boundary_intent = getattr(route, "boundary_intent", None)
    boundary_confidence = getattr(route, "boundary_confidence", None)
    # Any typed boundary metadata takes precedence over query continuation so
    # an attack, security bypass, or ambiguous boundary cannot be reinterpreted
    # as an answer to the active banking result.
    boundary_requested = response_key == "meta.melkor_easter_egg" or boundary_intent is not None
    if boundary_intent == "security_bypass":
        # Security bypasses use the established confirmation/PIN boundary;
        # they must never be dressed up as the playful Melkor response.
        response_key = "conversational.security_confirmation_required"
        raw_response = None
        boundary_requested = True
        logger.info("gate_security_boundary_enforced", boundary_intent=boundary_intent)
    if response_key == "meta.melkor_easter_egg" and not is_melkor_eligible(
        boundary_intent,
        boundary_confidence,
    ):
        logger.info(
            "gate_melkor_response_downgraded",
            boundary_intent=boundary_intent,
            confidence_band="high" if (boundary_confidence or 0.0) >= 0.75 else "low",
        )
        response_key = "conversational.clarify"
        raw_response = None
    elif response_key == "meta.melkor_easter_egg":
        logger.info(
            "gate_melkor_response_accepted",
            boundary_intent=boundary_intent,
            confidence_band="high",
        )
    had_active_query_session = await ctx.has_active_query_session()
    has_live_query_focus = ctx.state_view.active_domain == "query" or ctx.state_view.has_session_for_domain("query")
    active_query_should_own_direct_answer = canonical_decision == "direct_reply" or (
        canonical_decision == "direct_context_answer" and has_live_query_focus
    )
    if (
        had_active_query_session
        and active_query_should_own_direct_answer
        and not boundary_requested
        and response_key != "planner.cancelled"
        and response_key != "meta.melkor_easter_egg"
    ):
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
    if response_key:
        if response_key == "planner.cancelled":
            if has_cancelable_state(ctx.state):
                text = cancelled_message(ctx.state, locale)
                updates.update(await build_cancellation_reset_updates(ctx.state))
            else:
                text = clarify_message(ctx.state, locale)
        else:
            response_mode = map_response_key_to_mode(response_key)
            if response_mode is not None:
                extra_user_ctx: dict[str, object] = {}
                if response_key in SOCIAL_META_RESPONSE_KEYS:
                    extra_user_ctx = {
                        SOCIAL_META_RESPONSE_KEY_CTX: response_key,
                        SOCIAL_META_RENDER_PARAMS_CTX: {},
                    }
                elif response_key == "meta.melkor_easter_egg":
                    extra_user_ctx = {
                        "boundary_intent": boundary_intent,
                        "boundary_confidence": boundary_confidence,
                        "boundary_response_source": "semantic_router",
                    }
                text = _validated_semantic_reply(
                    ctx,
                    raw_response=raw_response,
                    mode=response_mode,
                    locale=locale,
                    extra_user_ctx=extra_user_ctx,
                )
                # A router may supply only an empathy preface.  The redirect
                # remains mandatory for an out-of-scope turn, so retain that
                # bounded prose and append the localized safe next step.
                if response_key == "conversational.out_of_scope":
                    text = format_out_of_scope_reply(locale, text)
            elif response_key == "conversational.out_of_scope":
                text = format_out_of_scope_reply(locale, raw_response)
            else:
                text = render_message(response_key, locale)
    else:
        text = raw_response or ""
        if not text:
            fallback_key: MessageKey = (
                "conversational.out_of_scope" if canonical_decision == "direct_reply" else "conversational.clarify"
            )
            text = render_message(fallback_key, locale)

    if had_active_query_session and not boundary_requested:
        updates.update(
            _build_query_session_exit_updates(
                ctx.state,
            )
        )
        logger.info("gate_query_session_exited_on_direct_reply")
    logger.info(
        "gate_semantic_router_direct_response",
        decision=canonical_decision,
        response_key=response_key,
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
