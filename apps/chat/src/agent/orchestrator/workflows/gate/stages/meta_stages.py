from typing import Any

from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_detection import detect_unsupported_capability
from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_presentation import (
    unsupported_capability_params,
)
from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_registry import get_unsupported_capability
from apps.chat.src.agent.orchestrator.conversation.conversation_grounding import (
    conversation_display_name,
    conversation_topic_for_response,
)
from apps.chat.src.agent.orchestrator.guardrails.banking_ambiguity import (
    classify_banking_coded_ambiguity,
    render_banking_coded_ambiguity_prompt,
)
from apps.chat.src.agent.orchestrator.guardrails.cancellation import clear_query_session
from apps.chat.src.agent.orchestrator.models.state import CapabilityBoundary
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.deterministic import (
    classify_deterministic_meta_response,
)
from apps.chat.src.agent.orchestrator.workflows.gate.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.locale_state import _locale_update
from apps.chat.src.agent.orchestrator.workflows.gate.query_session_exit import _build_query_session_exit_updates
from apps.chat.src.agent.orchestrator.workflows.gate.routing import (
    _route_observability_updates,
)
from shared.i18n.renderer import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def _stage_banking_ambiguity(ctx: GateContext) -> dict[str, Any] | None:
    """Deterministic ambiguous banking clarification."""
    ctx.ambiguous_banking_domain = classify_banking_coded_ambiguity(ctx.message_text)
    if (
        ctx.ambiguous_banking_domain is not None
        and not ctx.live_pending_interrupt
        and not ctx.state.has_quote
        and not ctx.state.session_stack
        and not ctx.state.waves
        and ctx.state.pending_interrupt is None
    ):
        logger.info(
            "gate_banking_coded_ambiguity_clarify",
            domain=ctx.ambiguous_banking_domain,
        )
        return {
            **ctx.gate_updates,
            "direct_path_triggered": True,
            "final_response": render_banking_coded_ambiguity_prompt(ctx.message_text, locale=ctx.current_locale),
            "semantic_path_shape": "banking_coded_ambiguity_clarify",
            **_route_observability_updates(
                owner="guardrail",
                decision=f"banking_coded_ambiguity_{ctx.ambiguous_banking_domain}",
            ),
        }
    return None


async def _stage_deterministic_meta(ctx: GateContext) -> dict[str, Any] | None:
    """Deterministic meta response (greeting, appreciation, identity, etc.)."""
    if ctx.live_pending_interrupt or ctx.state.has_quote:
        return None
    deterministic_meta = classify_deterministic_meta_response(ctx.message_text)
    if not deterministic_meta:
        return None
    response_key = deterministic_meta.response_key
    response_locale = deterministic_meta.response_locale
    locale = response_locale or ctx.current_locale
    locale_updates = _locale_update(ctx.state, locale) if response_locale else {}
    await ctx.ensure_query_session()
    exit_updates = _build_query_session_exit_updates(
        ctx.state,
        query_session_snapshot=ctx.query_session_snapshot,
    )
    if (
        ctx.redis_client
        and isinstance(ctx.query_session_snapshot, dict)
        and ctx.query_session_snapshot.get("session_active")
    ):
        await clear_query_session(ctx.redis_client, ctx.state.phone_number)
    if exit_updates:
        logger.info("gate_query_session_exited_on_direct_reply", had_pending_clarification=False)
    capability_boundary_updates: dict[str, Any] = {}
    render_params = deterministic_meta.params
    if response_key == "capability.unsupported_unavailable":
        capability = None
        if deterministic_meta.params:
            capability = get_unsupported_capability(str(deterministic_meta.params.get("capability_key") or ""))
        capability = capability or detect_unsupported_capability(ctx.message_text)
        if capability is not None:
            render_params = unsupported_capability_params(capability, locale=locale)
            capability_boundary_updates["capability_boundary"] = CapabilityBoundary(
                key=capability.key,
                label=capability.label,
            )
    if (
        response_key == "conversational.greeting"
        and not ctx.state.pending_interrupt
        and not ctx.state.session_stack
        and not ctx.state.waves
    ):
        loaded_context = ctx.state.loaded_context if isinstance(ctx.state.loaded_context, dict) else None
        display_name = conversation_display_name(loaded_context)
        if display_name:
            response_key = "conversational.greeting_named"
            render_params = {**(render_params or {}), "display_name": display_name}
    final_response = render_message(response_key, locale, render_params)
    return {
        **ctx.gate_updates,
        **exit_updates,
        **locale_updates,
        **capability_boundary_updates,
        "direct_path_triggered": True,
        "final_response": final_response,
        "conversation_topic": conversation_topic_for_response(
            final_response,
            response_key=response_key,
            semantic_path_shape="meta_direct",
        ),
        "semantic_path_shape": "meta_direct",
        **_route_observability_updates(owner="guardrail", decision="meta_direct"),
    }
