from typing import Any

from apps.chat.src.agent.orchestrator.conversation.conversation_responder_text import is_banking_refusal_reply
from apps.chat.src.agent.orchestrator.guardrails.banking_ambiguity import render_banking_coded_ambiguity_prompt
from apps.chat.src.agent.orchestrator.guardrails.cancellation import (
    build_cancellation_reset_updates,
    cancelled_message,
    clarify_message,
    clear_query_session,
    has_cancelable_state,
)
from apps.chat.src.agent.orchestrator.presentation.conversational_style import format_out_of_scope_reply
from apps.chat.src.agent.orchestrator.workflows.gate.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.direct_tasks import _build_direct_domain_task
from apps.chat.src.agent.orchestrator.workflows.gate.locale_state import _effective_response_locale
from apps.chat.src.agent.orchestrator.workflows.gate.query_session_exit import _build_query_session_exit_updates
from apps.chat.src.agent.orchestrator.workflows.gate.routing import (
    _route_observability_updates,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.helpers import _build_bounded_conversational_reply
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
    if had_active_query_session and canonical_decision == "direct_reply" and route.response_key != "planner.cancelled":
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
        if route.response_key == "conversational.casual_chat":
            text = await _build_bounded_conversational_reply(ctx, locale) or render_message(
                "conversational.out_of_scope",
                locale,
            )
        elif route.response_key == "conversational.out_of_scope":
            responder_reply = None
            if canonical_decision == "direct_reply" and (
                not route.response or is_banking_refusal_reply(route.response, locale=locale)
            ):
                responder_reply = await _build_bounded_conversational_reply(ctx, locale)
            text = responder_reply or format_out_of_scope_reply(locale, route.response)
        elif route.response_key == "planner.cancelled":
            if has_cancelable_state(ctx.state):
                text = cancelled_message(ctx.state, locale)
                updates.update(await build_cancellation_reset_updates(ctx.state, ctx.redis_client))
            else:
                text = clarify_message(ctx.state, locale)
        else:
            text = render_message(route.response_key, locale)
    else:
        text = route.response or ""
        if not text:
            responder_reply = await _build_bounded_conversational_reply(ctx, locale)
            if responder_reply:
                text = responder_reply
        if not text:
            fallback_key: MessageKey = (
                "conversational.out_of_scope" if canonical_decision == "direct_reply" else "conversational.clarify"
            )
            text = render_message(fallback_key, locale)

    had_pending_query_clarification = bool(
        isinstance(ctx.query_session_snapshot, dict) and ctx.query_session_snapshot.get("pending_clarification")
    )
    if had_active_query_session:
        await clear_query_session(ctx.redis_client, ctx.state_view.phone_number)
        updates.update(
            _build_query_session_exit_updates(
                ctx.state,
                query_session_snapshot=ctx.query_session_snapshot,
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
