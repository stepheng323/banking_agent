"""Main pipeline logic for the semantic router."""

from typing import Any

from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_presentation import (
    unsupported_capability_params,
)
from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_registry import (
    get_unsupported_capability,
)
from apps.chat.src.agent.orchestrator.models.state import CapabilityBoundary
from apps.chat.src.agent.orchestrator.workflows.gate.core.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.core.routing import (
    _route_observability_updates,
    _semantic_route_decision,
    _semantic_route_mode,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.semantic_routing.classifier import (
    _classify_semantic_route,
    _semantic_router_can_run,
    _skip_semantic_router_for_interrupt,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.semantic_routing.control_handlers import (
    semantic_cancel_updates,
    semantic_executor_handoff_updates,
    semantic_locale_switch_updates,
    semantic_route_executor_updates,
    semantic_schedule_target_updates,
    support_hint_veto_updates,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.semantic_routing.direct_response import (
    _handle_semantic_direct_response,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.semantic_routing.domain_dispatch import (
    _handle_semantic_domain_dispatch,
)
from apps.chat.src.agent.orchestrator.workflows.gate.state.query_session_exit import (
    _build_query_session_exit_updates,
)
from banking.presentation.i18n.renderer import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def _handle_semantic_route(ctx: GateContext, route: Any) -> dict[str, Any] | None:
    canonical_decision = _semantic_route_decision(route)
    canonical_mode = _semantic_route_mode(route)
    if veto_updates := support_hint_veto_updates(
        ctx,
        canonical_decision=canonical_decision,
        canonical_mode=canonical_mode,
    ):
        return veto_updates

    if locale_updates := await semantic_locale_switch_updates(
        ctx,
        route,
        canonical_decision=canonical_decision,
        canonical_mode=canonical_mode,
    ):
        return locale_updates

    updates, expected_executors = semantic_route_executor_updates(route)

    if ctx.live_pending_interrupt:
        route = None
        canonical_decision = None
        canonical_mode = None

    if route is not None:
        unsupported_cap = getattr(route, "unsupported_capability", None)
        if unsupported_cap is not None:


            capability = get_unsupported_capability(unsupported_cap)
            if capability is not None:
                params = unsupported_capability_params(capability, locale=ctx.current_locale)
                logger.info("gate_semantic_router_unsupported_capability", capability_key=capability.key)
                return {
                    **ctx.gate_updates,
                    **(ctx.summary_updates or {}),
                    "capability_boundary": CapabilityBoundary(key=capability.key, label=capability.label),
                    "direct_path_triggered": True,
                    "final_response": render_message(
                        "capability.unsupported_unavailable",
                        ctx.current_locale,
                        params,
                    ),
                    "semantic_path_shape": "semantic_unsupported_capability",
                    **_route_observability_updates(
                        owner="guardrail",
                        decision="semantic_unsupported_capability",
                    ),
                    **updates,
                }

    if route is not None:
        cancel_updates = await semantic_cancel_updates(
            ctx,
            route,
            updates=updates,
            canonical_decision=canonical_decision,
            canonical_mode=canonical_mode,
        )
        if cancel_updates is not None:
            return cancel_updates

    if route is not None:
        schedule_updates = semantic_schedule_target_updates(
            ctx,
            route,
            updates=updates,
            canonical_decision=canonical_decision,
            canonical_mode=canonical_mode,
        )
        if schedule_updates is not None:
            return schedule_updates

    if route is not None:
        direct_response = await _handle_semantic_direct_response(
            ctx,
            route=route,
            updates=updates,
            canonical_decision=canonical_decision,
            canonical_mode=canonical_mode,
        )
        if direct_response is not None:
            return direct_response

    if route is not None:
        domain_dispatch = await _handle_semantic_domain_dispatch(
            ctx,
            route=route,
            updates=updates,
            canonical_decision=canonical_decision,
            canonical_mode=canonical_mode,
        )
        if domain_dispatch is not None:
            return domain_dispatch

    if updates:
        logger.info("gate_semantic_router_expected_executors", executors=expected_executors)
        if await ctx.has_active_query_session():
            updates.update(
                _build_query_session_exit_updates(
                    ctx.state,
                )
            )
        return semantic_executor_handoff_updates(
            ctx,
            updates=updates,
            canonical_decision=canonical_decision,
            canonical_mode=canonical_mode,
        )
    return None


async def _stage_semantic_router(ctx: GateContext) -> dict[str, Any] | None:
    """LLM semantic router dispatch."""
    await ctx.ensure_turn_summary()
    assert ctx.turn_summary is not None  # noqa: S101 – ensured by ensure_turn_summary

    interrupt_kind = ctx.state_view.pending_interrupt_kind
    skip_semantic_router_for_interrupt = _skip_semantic_router_for_interrupt(ctx)

    if skip_semantic_router_for_interrupt:
        logger.info(
            "gate_semantic_router_skipped_for_interrupt",
            kind=interrupt_kind,
            task_ids=ctx.state_view.pending_interrupt_task_ids,
        )

    if not _semantic_router_can_run(ctx, skip_for_interrupt=skip_semantic_router_for_interrupt):
        return None

    route = await _classify_semantic_route(ctx)
    if route is None:
        return None
    return await _handle_semantic_route(ctx, route)
