from time import time
from typing import Any

from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_detection import detect_unsupported_capability
from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_models import (
    UnsupportedBoundaryTurnOutput,
    UnsupportedCapability,
)
from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_presentation import (
    unsupported_capability_params,
)
from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_registry import get_unsupported_capability
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.casual import (
    looks_like_obvious_casual_or_meta_turn,
)
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.direct_domains import _is_query_domain_request
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.query_followups import (
    _query_followup_bypass_reason,
)
from apps.chat.src.agent.orchestrator.workflows.gate.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.routing import _route_observability_updates
from apps.chat.src.agent.orchestrator.workflows.gate.unsupported_capability_routing import (
    FOLLOWUP_CONVERSATIONAL_LIMIT,
    boundary_update,
    coerce_boundary,
    is_explicit_supported_banking_request,
    is_live_boundary,
    is_supported_banking_request,
    looks_like_boundary_followup,
    recent_unsupported_boundary,
    semantic_boundary_turn,
    semantic_unsupported_capability,
)
from banking.presentation.i18n.renderer import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def _query_can_own_boundary_turn(ctx: GateContext) -> bool:
    if _is_query_domain_request(ctx.message_text):
        return True

    await ctx.ensure_query_session()
    bypass_reason, _ = _query_followup_bypass_reason(
        message_text=ctx.message_text,
        locale=ctx.current_locale,
        query_session_snapshot=ctx.query_session_snapshot if isinstance(ctx.query_session_snapshot, dict) else None,
        has_context_frames=ctx.state_view.has_context_frames,
    )
    return bypass_reason is not None


async def _stage_capability_boundary_followup(ctx: GateContext) -> dict[str, Any] | None:
    """Handle short follow-ups after unsupported capability refusals before stale context reuse."""
    if ctx.live_pending_interrupt or ctx.state_view.has_gate_blocking_state:
        return None

    now = time()
    boundary = coerce_boundary(ctx.state_view.capability_boundary)
    if boundary is not None and not is_live_boundary(boundary, now=now):
        ctx.gate_updates["capability_boundary"] = None
        return None
    if boundary is None:
        boundary = recent_unsupported_boundary(ctx)

    capability = get_unsupported_capability(boundary.key) if boundary is not None else None

    if boundary is None or capability is None:
        return None
    if await _query_can_own_boundary_turn(ctx):
        ctx.gate_updates["capability_boundary"] = None
        logger.info(
            "gate_unsupported_capability_boundary_skipped_for_query_turn",
            capability_key=capability.key,
        )
        return None

    if looks_like_obvious_casual_or_meta_turn(ctx.message_text):
        ctx.gate_updates["capability_boundary"] = None
        logger.info(
            "gate_unsupported_capability_boundary_skipped_for_casual_turn",
            capability_key=capability.key,
        )
        return None

    detected_capability = detect_unsupported_capability(ctx.message_text)
    semantic_capability: UnsupportedCapability | None = None
    looks_like_followup = looks_like_boundary_followup(ctx.message_text, capability)
    if detected_capability is not None and detected_capability.key != capability.key:
        ctx.gate_updates["capability_boundary"] = None
        return None

    if detected_capability is None and is_explicit_supported_banking_request(ctx.message_text):
        ctx.gate_updates["capability_boundary"] = None
        return None

    boundary_decision: UnsupportedBoundaryTurnOutput | None = None
    if detected_capability is None and not looks_like_followup:
        boundary_decision = await semantic_boundary_turn(ctx, boundary=boundary, capability=capability)
        if boundary_decision is not None:
            if boundary_decision.action == "same_unsupported":
                looks_like_followup = True
            elif boundary_decision.action in {"new_unsupported", "supported_banking", "unrelated"}:
                ctx.gate_updates["capability_boundary"] = None
                return None

    if (
        detected_capability is None
        and boundary_decision is None
        and not looks_like_followup
        and is_supported_banking_request(ctx.message_text)
    ):
        ctx.gate_updates["capability_boundary"] = None
        return None

    if detected_capability is None:
        semantic_capability = await semantic_unsupported_capability(ctx, ctx.message_text)
        if semantic_capability is not None and semantic_capability.key != capability.key:
            ctx.gate_updates["capability_boundary"] = None
            return None

    if semantic_capability is None and not looks_like_followup:
        ctx.gate_updates["capability_boundary"] = None
        return None

    next_count = boundary.followup_count + 1
    updated_boundary = boundary_update(boundary, followup_count=next_count, now=now)
    params = unsupported_capability_params(capability, locale=ctx.current_locale)
    response: str | None
    if next_count > FOLLOWUP_CONVERSATIONAL_LIMIT:
        response = render_message("capability.unsupported_unavailable_firm", ctx.current_locale, params)
    else:
        response = render_message("capability.unsupported_unavailable_followup", ctx.current_locale, params)

    logger.info(
        "gate_unsupported_capability_followup",
        capability_key=capability.key,
        followup_count=next_count,
        firm=next_count > FOLLOWUP_CONVERSATIONAL_LIMIT,
    )
    return {
        **ctx.gate_updates,
        "capability_boundary": updated_boundary,
        "direct_path_triggered": True,
        "final_response": response,
        "semantic_path_shape": "capability_boundary_followup",
        **_route_observability_updates(
            owner="guardrail",
            decision="unsupported_capability_followup",
        ),
    }
