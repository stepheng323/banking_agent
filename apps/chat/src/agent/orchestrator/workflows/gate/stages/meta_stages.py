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
from apps.chat.src.agent.orchestrator.conversation.conversation_responder_modes import (
    SOCIAL_META_RENDER_PARAMS_CTX,
    SOCIAL_META_RESPONSE_KEY_CTX,
    SOCIAL_META_RESPONSE_KEYS,
    ConversationResponseMode,
    map_response_key_to_mode,
)
from apps.chat.src.agent.orchestrator.conversation.conversation_responder_text import (
    redirect_text,
)
from apps.chat.src.agent.orchestrator.guardrails.banking_ambiguity import (
    classify_banking_coded_ambiguity,
    render_banking_coded_ambiguity_prompt,
)
from apps.chat.src.agent.orchestrator.models.state import CapabilityBoundary
from apps.chat.src.agent.orchestrator.models.turn_directive import RouteResolution
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.deterministic import (
    classify_deterministic_meta_response,
)
from apps.chat.src.agent.orchestrator.workflows.gate.core.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.core.outcomes import direct_response
from apps.chat.src.agent.orchestrator.workflows.gate.stages.helpers import _build_bounded_conversational_reply
from apps.chat.src.agent.orchestrator.workflows.gate.state.locale_state import _locale_update
from apps.chat.src.agent.orchestrator.workflows.gate.state.query_session_exit import (
    _build_query_session_exit_updates,
)
from banking.presentation.i18n.renderer import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _classify_banking_ambiguity(ctx: GateContext) -> str | None:
    ctx.ambiguous_banking_domain = classify_banking_coded_ambiguity(ctx.message_text)
    return ctx.ambiguous_banking_domain


def _banking_ambiguity_can_clarify(ctx: GateContext, ambiguous_domain: str | None) -> bool:
    return (
        ambiguous_domain is not None and not ctx.live_pending_interrupt and not ctx.state_view.has_gate_blocking_state
    )


def _banking_ambiguity_updates(ctx: GateContext, ambiguous_domain: str) -> RouteResolution:
    logger.info(
        "gate_banking_coded_ambiguity_clarify",
        domain=ambiguous_domain,
    )
    return direct_response(
        ctx,
        response=render_banking_coded_ambiguity_prompt(ctx.message_text, locale=ctx.current_locale),
        owner="guardrail",
        decision=f"banking_coded_ambiguity_{ambiguous_domain}",
        path_shape="banking_coded_ambiguity_clarify",
    )


async def _stage_banking_ambiguity(ctx: GateContext) -> RouteResolution | None:
    """Deterministic ambiguous banking clarification."""
    ambiguous_domain = _classify_banking_ambiguity(ctx)
    if ambiguous_domain is not None and _banking_ambiguity_can_clarify(ctx, ambiguous_domain):
        return _banking_ambiguity_updates(ctx, ambiguous_domain)
    return None


async def _stage_deterministic_meta(ctx: GateContext) -> RouteResolution | None:
    """Deterministic meta response (greeting, appreciation, identity, etc.)."""
    if ctx.live_pending_interrupt or ctx.state_view.has_quote:
        return None
    deterministic_meta = classify_deterministic_meta_response(ctx.message_text)
    if not deterministic_meta:
        return None
    response_key = deterministic_meta.response_key
    response_locale = deterministic_meta.response_locale
    locale = response_locale or ctx.current_locale
    locale_updates = _locale_update(ctx.state_view, locale) if response_locale else {}

    capability_boundary_updates: dict[str, Any] = {}
    unsupported_user_ctx: dict[str, object] = {}
    render_params = deterministic_meta.params
    if response_key == "capability.unsupported_unavailable":
        capability = None
        if deterministic_meta.params:
            capability = get_unsupported_capability(str(deterministic_meta.params.get("capability_key") or ""))
        capability = capability or detect_unsupported_capability(ctx.message_text)
        if capability is not None:
            render_params = unsupported_capability_params(capability, locale=locale)
            unsupported_user_ctx["unsupported_capability"] = {
                "key": capability.key,
                "label": render_params["capability"],
                "supported_alternatives": render_params["supported"],
            }
            capability_boundary_updates["capability_boundary"] = CapabilityBoundary(
                key=capability.key,
                label=capability.label,
            )
    if (
        response_key == "conversational.greeting"
        and not ctx.state_view.has_pending_interrupt
        and not ctx.state_view.has_session_stack
        and not ctx.state_view.has_waves
    ):
        loaded_context = ctx.state_view.loaded_context_or_empty
        display_name = conversation_display_name(loaded_context)
        if display_name:
            response_key = "conversational.greeting_named"
            render_params = {**(render_params or {}), "display_name": display_name}
    if isinstance(render_params, dict) and render_params.get("casual_kind") == "joke":
        final_response = await _build_bounded_conversational_reply(
            ctx,
            locale,
            mode=ConversationResponseMode.CASUAL,
        ) or redirect_text(locale, casual_streak=0)
    elif (response_mode := map_response_key_to_mode(response_key)) is not None:
        extra_user_ctx: dict[str, Any] = unsupported_user_ctx
        if response_key in SOCIAL_META_RESPONSE_KEYS:
            extra_user_ctx = {
                SOCIAL_META_RESPONSE_KEY_CTX: response_key,
                SOCIAL_META_RENDER_PARAMS_CTX: render_params or {},
            }
        final_response = await _build_bounded_conversational_reply(
            ctx,
            locale,
            mode=response_mode,
            extra_user_ctx=extra_user_ctx,
        ) or render_message(response_key, locale, render_params)
    else:
        final_response = render_message(response_key, locale, render_params)

    exit_updates = {}
    if await ctx.has_active_query_session():
        exit_updates = _build_query_session_exit_updates(ctx.state)
        logger.info("gate_query_session_exited_on_direct_reply")

    social_context_updates: dict[str, Any] = {}
    if response_key in SOCIAL_META_RESPONSE_KEYS:
        social_context_updates["capability_boundary"] = None
        logger.info("conversation_social_context_reset", response_key=response_key)

    return direct_response(
        ctx,
        response=final_response,
        owner="guardrail",
        decision="meta_direct",
        path_shape="meta_direct",
        extra_updates={
            **locale_updates,
            **capability_boundary_updates,
            **social_context_updates,
            **exit_updates,
            "conversation_topic": conversation_topic_for_response(
                final_response,
                response_key=response_key,
                path_shape="meta_direct",
            ),
        },
    )
