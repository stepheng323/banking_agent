"""Main pipeline logic for the semantic router."""

import inspect
from typing import Any

from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_detection import (
    detect_unsupported_capability,
    should_try_semantic_unsupported_capability,
)
from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_presentation import (
    unsupported_capability_params,
)
from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_registry import (
    get_unsupported_capability,
)
from apps.chat.src.agent.orchestrator.models.state import CapabilityBoundary
from apps.chat.src.agent.orchestrator.models.turn_directive import RouteResolution
from apps.chat.src.agent.orchestrator.workflows.gate.core.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.core.outcomes import direct_response, policy_block
from apps.chat.src.agent.orchestrator.workflows.gate.core.routing import (
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
from apps.chat.src.agent.orchestrator.workflows.gate.state.locale_state import _effective_response_locale
from apps.chat.src.agent.orchestrator.workflows.gate.state.query_session_exit import (
    _build_query_session_exit_updates,
)
from banking.presentation.i18n.renderer import render_message
from shared.observability.llm import LLMCallDeadlineExceeded
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def _apply_semantic_route_locale(ctx: GateContext, route: Any) -> None:
    """Apply one detected-language decision before any semantic outcome renders."""
    locale, locale_updates = await _effective_response_locale(
        state_view=ctx.state_view,
        redis_client=ctx.redis_client,
        detected_language=getattr(route, "detected_language", None),
        confidence=float(getattr(route, "confidence", 0.0) or 0.0),
    )
    if not locale_updates:
        return

    ctx.current_locale = locale
    ctx.gate_updates.update(locale_updates)
    tracker = ctx.progress_tracker
    set_locale = getattr(tracker, "set_locale", None)
    if callable(set_locale):
        result = set_locale(locale)
        if inspect.isawaitable(result):
            await result


async def _handle_semantic_route(ctx: GateContext, route: Any) -> RouteResolution | None:
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

    await _apply_semantic_route_locale(ctx, route)

    updates, expected_executors = semantic_route_executor_updates(route)

    if ctx.live_pending_interrupt:
        route = None
        canonical_decision = None
        canonical_mode = None

    context_followup = getattr(route, "context_followup", None) if route is not None else None
    if context_followup is not None and not ctx.live_pending_interrupt:
        # The semantic router has already interpreted this against the compact
        # displayed-frame bundle.  Ground the returned selector locally rather
        # than calling the retired context-frame interpreter again.
        from apps.chat.src.agent.orchestrator.workflows.gate.stages.context_frame_stages import (
            resolve_semantic_context_followup,
        )

        context_resolution = resolve_semantic_context_followup(
            ctx,
            context_followup,
            replay_modifier=getattr(route, "context_replay_modifier", None),
        )
        if context_resolution is not None:
            logger.info(
                "gate_semantic_context_followup_resolved",
                action=context_followup.decision,
                confidence=context_followup.confidence,
            )
            return context_resolution
        logger.info(
            "gate_semantic_context_followup_unresolved",
            action=context_followup.decision,
            confidence=context_followup.confidence,
        )

    if route is not None:
        unsupported_cap = getattr(route, "unsupported_capability", None)
        has_supported_route = bool(
            getattr(route, "target_intent", None)
            or getattr(route, "read_request", None)
            or getattr(route, "expected_transaction_executors", None)
        )
        if unsupported_cap is not None and not has_supported_route:
            capability = get_unsupported_capability(unsupported_cap)
            if capability is not None:
                params = unsupported_capability_params(capability, locale=ctx.current_locale)
                logger.info("gate_semantic_router_unsupported_capability", capability_key=capability.key)
                return policy_block(
                    ctx,
                    response=render_message(
                        "capability.unsupported_unavailable",
                        ctx.current_locale,
                        params,
                    ),
                    decision="semantic_unsupported_capability",
                    path_shape="semantic_unsupported_capability",
                    extra_updates={
                        **(ctx.summary_updates or {}),
                        "capability_boundary": CapabilityBoundary(key=capability.key, label=capability.label),
                        **updates,
                    },
                )
        elif unsupported_cap is not None:
            logger.info("gate_semantic_router_unsupported_capability_discarded_for_supported_route")

    if (
        canonical_decision == "planner_ambiguous"
        and detect_unsupported_capability(ctx.message_text) is None
        and should_try_semantic_unsupported_capability(ctx.message_text)
    ):
        logger.info("gate_semantic_router_unsupported_candidate_clarified")
        return direct_response(
            ctx,
            response=render_message("conversational.clarify", ctx.current_locale),
            owner="semantic_router",
            decision="semantic_unsupported_candidate_clarify",
            source="semantic_router",
            path_shape="semantic_unsupported_candidate_clarify",
            extra_updates={**(ctx.summary_updates or {}), **updates},
        )

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
        direct_resolution = await _handle_semantic_direct_response(
            ctx,
            route=route,
            updates=updates,
            canonical_decision=canonical_decision,
            canonical_mode=canonical_mode,
        )
        if direct_resolution is not None:
            return direct_resolution

    if route is not None:
        domain_dispatch = await _handle_semantic_domain_dispatch(
            ctx,
            route=route,
            updates=updates,
            canonical_decision=canonical_decision,
            canonical_mode=canonical_mode,  # type: ignore
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


async def resolve_semantic_route(ctx: GateContext) -> RouteResolution | None:
    """Resolve semantic evidence without invoking a registered gate stage."""
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

    try:
        route = await _classify_semantic_route(ctx)
    except LLMCallDeadlineExceeded as exc:
        logger.warning(
            "gate_semantic_router_deadline_exceeded",
            role=exc.role,
            deadline_seconds=exc.deadline_seconds,
        )
        return direct_response(
            ctx,
            response=render_message("orchestrator.fallback.router_timeout", ctx.current_locale),
            owner="semantic_router",
            decision="semantic_router_timeout",
            source="semantic_router",
            path_shape="semantic_router_timeout",
            extra_updates={**(ctx.summary_updates or {}), **ctx.gate_updates},
        )
    if route is None:
        return None
    return await _handle_semantic_route(ctx, route)


async def _stage_semantic_router(ctx: GateContext) -> RouteResolution | None:
    """Registered semantic-routing stage."""
    return await resolve_semantic_route(ctx)
