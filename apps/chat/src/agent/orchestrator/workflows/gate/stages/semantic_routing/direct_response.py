"""Direct response handler for the semantic router."""

from typing import Any

from apps.chat.src.agent.orchestrator.conversation.conversation_responder_modes import (
    SOCIAL_META_RENDER_PARAMS_CTX,
    SOCIAL_META_RESPONSE_KEY_CTX,
    SOCIAL_META_RESPONSE_KEYS,
    ConversationResponseMode,
    map_response_key_to_mode,
)
from apps.chat.src.agent.orchestrator.guardrails.banking_ambiguity import (
    render_banking_coded_ambiguity_prompt,
)
from apps.chat.src.agent.orchestrator.guardrails.cancellation import (
    build_cancellation_reset_updates,
    cancelled_message,
    clarify_message,
    has_cancelable_state,
)
from apps.chat.src.agent.orchestrator.presentation.conversational_style import (
    format_out_of_scope_reply,
)
from apps.chat.src.agent.orchestrator.workflows.gate.core.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.core.routing import (
    _route_observability_updates,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.helpers import (
    _build_bounded_conversational_reply,
)
from apps.chat.src.agent.orchestrator.workflows.gate.state.locale_state import (
    _effective_response_locale,
)
from apps.chat.src.agent.orchestrator.workflows.gate.state.query_session_exit import (
    _build_query_session_exit_updates,
)
from apps.chat.src.agent.orchestrator.workflows.gate.utils.direct_tasks import (
    _build_direct_domain_task,
)
from banking.presentation.i18n.message_keys import MessageKey
from banking.presentation.i18n.renderer import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def _handle_semantic_direct_response(
    ctx: GateContext,
    *,
    route: Any,
    updates: dict[str, Any],
    canonical_decision: str | None,
    canonical_mode: str | None,
) -> dict[str, Any] | None:
    if canonical_decision not in {"direct_reply", "direct_context_answer"}:
        return None

    locale, detected_locale_updates = await _effective_response_locale(
        state_view=ctx.state_view,
        redis_client=ctx.redis_client,
        detected_language=getattr(route, "detected_language", None),
    )
    updates.update(detected_locale_updates)
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
        return {
            **ctx.gate_updates,
            **(ctx.summary_updates or {}),
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
            **updates,
        }
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
        return {
            **ctx.gate_updates,
            **(ctx.summary_updates or {}),
            "direct_path_triggered": True,
            "final_response": render_banking_coded_ambiguity_prompt(ctx.message_text, locale=locale),
            "semantic_path_shape": "banking_coded_ambiguity_clarify",
            **_route_observability_updates(
                owner="guardrail",
                decision=f"banking_coded_ambiguity_{ctx.ambiguous_banking_domain}",
            ),
            **updates,
        }
    if route.response_key:
        if route.response_key == "planner.cancelled":
            if has_cancelable_state(ctx.state):
                text = cancelled_message(ctx.state, locale)
                updates.update(await build_cancellation_reset_updates(ctx.state, ctx.redis_client))
            else:
                text = clarify_message(ctx.state, locale)
        else:
            response_mode = map_response_key_to_mode(route.response_key)
            responder_reply = None
            if response_mode is not None:
                extra_user_ctx: dict[str, object] = {}
                if route.response_key in SOCIAL_META_RESPONSE_KEYS:
                    extra_user_ctx = {
                        SOCIAL_META_RESPONSE_KEY_CTX: route.response_key,
                        SOCIAL_META_RENDER_PARAMS_CTX: {},
                    }
                responder_reply = await _build_bounded_conversational_reply(
                    ctx,
                    locale,
                    mode=response_mode,
                    extra_user_ctx=extra_user_ctx,
                )
            if responder_reply:
                text = responder_reply
            elif route.response_key == "conversational.out_of_scope":
                text = format_out_of_scope_reply(locale, route.response)
            else:
                text = render_message(route.response_key, locale)
    else:
        text = route.response or ""
        if not text:
            responder_reply = await _build_bounded_conversational_reply(
                ctx,
                locale,
                mode=ConversationResponseMode.CASUAL,
            )
            if responder_reply:
                text = responder_reply
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
    return {
        **ctx.gate_updates,
        **(ctx.summary_updates or {}),
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
