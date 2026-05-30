from typing import Any

from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_detection import detect_unsupported_capability
from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_presentation import (
    unsupported_capability_params,
)
from apps.chat.src.agent.orchestrator.models.state import CapabilityBoundary
from apps.chat.src.agent.orchestrator.workflows.gate.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.routing import _route_observability_updates
from apps.chat.src.agent.orchestrator.workflows.gate.unsupported_capability_routing import (
    is_supported_banking_request,
    semantic_unsupported_capability,
)
from banking.presentation.i18n.renderer import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def _stage_semantic_unsupported_capability(ctx: GateContext) -> dict[str, Any] | None:
    """Semantic fallback for unsupported capability boundaries not caught by registry phrases."""
    if (
        ctx.live_pending_interrupt
        or ctx.state.pending_interrupt is not None
        or ctx.state.has_quote
        or ctx.state.session_stack
        or ctx.state.waves
        or not ctx.phrase_heavy_fastpath_allowed
    ):
        return None
    if detect_unsupported_capability(ctx.message_text) is not None:
        return None
    if is_supported_banking_request(ctx.message_text):
        return None

    capability = await semantic_unsupported_capability(ctx, ctx.message_text)
    if capability is None:
        return None

    params = unsupported_capability_params(capability, locale=ctx.current_locale)
    logger.info("gate_semantic_unsupported_capability", capability_key=capability.key)
    return {
        **ctx.gate_updates,
        "capability_boundary": CapabilityBoundary(key=capability.key, label=capability.label),
        "direct_path_triggered": True,
        "final_response": render_message("capability.unsupported_unavailable", ctx.current_locale, params),
        "semantic_path_shape": "semantic_unsupported_capability",
        **_route_observability_updates(
            owner="guardrail",
            decision="semantic_unsupported_capability",
        ),
    }
