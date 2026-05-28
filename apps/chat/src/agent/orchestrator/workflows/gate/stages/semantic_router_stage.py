from typing import Any

from apps.chat.src.agent.orchestrator.workflows.gate.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.interrupt_state import _is_numeric_input_interrupt_selection
from apps.chat.src.agent.orchestrator.workflows.gate.router_context import (
    _build_semantic_router_context,
    _should_invoke_semantic_router,
)
from apps.chat.src.agent.orchestrator.workflows.gate.routing import (
    _semantic_route_decision,
    _semantic_route_mode,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.semantic_direct_response import (
    _handle_semantic_direct_response,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.semantic_domain_dispatch import (
    _handle_semantic_domain_dispatch,
)
from apps.chat.src.agent.orchestrator.workflows.gate.stages.semantic_route_control import (
    append_routing_hints,
    semantic_cancel_updates,
    semantic_executor_handoff_updates,
    semantic_locale_switch_updates,
    semantic_route_executor_updates,
    semantic_schedule_target_updates,
    support_hint_veto_updates,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def _stage_semantic_router(ctx: GateContext) -> dict[str, Any] | None:
    """LLM semantic router dispatch."""
    await ctx.ensure_turn_summary()
    assert ctx.turn_summary is not None  # noqa: S101 – ensured by ensure_turn_summary

    interrupt_kind = getattr(ctx.state.pending_interrupt, "kind", None)
    skip_semantic_router_for_interrupt = ctx.live_pending_interrupt and (
        interrupt_kind in {"confirmation", "auth"} or _is_numeric_input_interrupt_selection(ctx.state, ctx.message_text)
    )

    if skip_semantic_router_for_interrupt:
        logger.info(
            "gate_semantic_router_skipped_for_interrupt",
            kind=interrupt_kind,
            task_ids=getattr(ctx.state.pending_interrupt, "task_ids", None),
        )

    if (
        not skip_semantic_router_for_interrupt
        and not ctx.state.has_quote
        and ctx.task_planner is not None
        and (ctx.live_pending_interrupt or _should_invoke_semantic_router(ctx.message_text))
    ):
        try:
            route_context = _build_semantic_router_context(
                ctx.turn_summary,
                ctx.state.preplanner_expected_transaction_executors,
                message_text=ctx.message_text,
            )
            route_context = append_routing_hints(route_context, ctx.routing_hints)
            route = await ctx.task_planner.route_semantic_turn(
                ctx.state.phone_number,
                ctx.message_text,
                context=route_context,
                path_label="direct_path",
            )
        except Exception as exc:
            logger.warning("gate_semantic_router_failed", error=str(exc))
            route = None

        if route is not None:
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
                return semantic_executor_handoff_updates(
                    ctx,
                    updates=updates,
                    canonical_decision=canonical_decision,
                    canonical_mode=canonical_mode,
                )

    return None
