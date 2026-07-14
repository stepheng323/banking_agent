"""Layered routing engine for the orchestrator gate."""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping

from apps.chat.src.agent.orchestrator.models.turn_directive import (
    RouteResolution,
    RoutingContractError,
    TurnDirective,
)
from apps.chat.src.agent.orchestrator.workflows.gate.core.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.core.contracts import (
    GATE_LAYER_INDEX,
    GateEligibilityResult,
    GateEngineResult,
    GateHandlerSpec,
    GateLayer,
    GateTraceEntry,
)
from apps.chat.src.agent.orchestrator.workflows.gate.core.outcomes import planner_handoff
from apps.chat.src.agent.orchestrator.workflows.gate.core.trace import summarize_gate_trace
from shared.utils.logging import get_logger, log_orchestrator_diagnostic

logger = get_logger(__name__)


def ordered_gate_handlers(handlers: Iterable[GateHandlerSpec]) -> tuple[GateHandlerSpec, ...]:
    """Return handlers in deterministic layered execution order."""
    return tuple(sorted(handlers, key=lambda spec: (GATE_LAYER_INDEX[spec.layer], spec.priority)))


def _extract_directive(updates: Mapping[str, object] | RouteResolution) -> TurnDirective | None:
    if isinstance(updates, RouteResolution):
        return updates.directive
    value = updates.get("turn_directive")
    return value if isinstance(value, TurnDirective) else None


def _log_engine_result(result: GateEngineResult) -> None:
    log_orchestrator_diagnostic(
        logger,
        "gate_engine_trace_summary",
        **summarize_gate_trace(result),
    )


def _build_trace_entry(
    spec: GateHandlerSpec,
    *,
    duration_ms: float,
    matched: bool,
    executed: bool,
    gate_updates_changed: bool,
    routing_updates: Mapping[str, object],
    skip_reason: str | None = None,
    skip_details: dict[str, object] | None = None,
) -> GateTraceEntry:
    return GateTraceEntry(
        handler_id=spec.id,
        layer=spec.layer,
        duration_ms=duration_ms,
        matched=matched,
        executed=executed,
        gate_updates_changed=gate_updates_changed,
        turn_directive=_extract_directive(routing_updates),
        skip_reason=skip_reason,
        skip_details=skip_details,
    )


def _default_eligibility() -> GateEligibilityResult:
    return GateEligibilityResult(eligible=True, reason="default_eligible", details={})


def _validate_resolution(spec: GateHandlerSpec, resolution: RouteResolution) -> dict[str, object]:
    directive = resolution.directive

    allowed_owners = spec.resolved_allowed_owners
    if directive.owner not in allowed_owners:
        allowed = ", ".join(sorted(allowed_owners)) or "none"
        raise RoutingContractError(
            f"gate handler {spec.id} returned owner {directive.owner}; allowed owners: {allowed}"
        )

    allowed_outcomes = spec.resolved_allowed_outcomes
    if directive.outcome_kind not in allowed_outcomes:
        allowed = ", ".join(sorted(item.value for item in allowed_outcomes)) or "none"
        raise RoutingContractError(
            f"gate handler {spec.id} returned {directive.outcome_kind.value}; allowed outcomes: {allowed}"
        )

    allowed_next_steps = spec.resolved_allowed_next_steps
    if directive.next_step not in allowed_next_steps:
        allowed = ", ".join(sorted(item.value for item in allowed_next_steps)) or "none"
        raise RoutingContractError(
            f"gate handler {spec.id} returned next step {directive.next_step.value}; allowed next steps: {allowed}"
        )

    return resolution.materialize()


async def run_gate_engine(ctx: GateContext, handlers: Iterable[GateHandlerSpec]) -> GateEngineResult:
    """Run layered gate handlers and return the first matching updates."""
    trace: list[GateTraceEntry] = []

    for spec in ordered_gate_handlers(handlers):
        before_updates = dict(ctx.gate_updates)
        eligibility_start = time.perf_counter()
        try:
            eligibility = spec.eligibility(ctx) if spec.eligibility is not None else _default_eligibility()
        except Exception:
            logger.exception("gate_engine_eligibility_failed", handler_id=spec.id, layer=spec.layer.value)
            raise
        eligibility_duration_ms = (time.perf_counter() - eligibility_start) * 1000
        if not eligibility.eligible:
            trace.append(
                _build_trace_entry(
                    spec,
                    duration_ms=eligibility_duration_ms,
                    matched=False,
                    executed=False,
                    gate_updates_changed=ctx.gate_updates != before_updates,
                    routing_updates=ctx.gate_updates,
                    skip_reason=eligibility.reason,
                    skip_details=eligibility.details,
                )
            )
            continue

        start = time.perf_counter()
        try:
            updates = await spec.handler(ctx)
        except Exception:
            logger.exception("gate_engine_handler_failed", handler_id=spec.id, layer=spec.layer.value)
            raise
        duration_ms = (time.perf_counter() - start) * 1000
        matched = updates is not None
        gate_updates_changed = ctx.gate_updates != before_updates
        routing_updates = updates if updates is not None else ctx.gate_updates
        trace.append(
            _build_trace_entry(
                spec,
                duration_ms=duration_ms,
                matched=matched,
                executed=True,
                gate_updates_changed=gate_updates_changed,
                routing_updates=routing_updates,
            )
        )

        if updates is not None:
            if not isinstance(updates, RouteResolution):
                raise RoutingContractError(
                    f"gate handler {spec.id} returned {type(updates).__name__}; expected RouteResolution"
                )
            committed_updates = _validate_resolution(spec, updates)
            result = GateEngineResult(
                updates=committed_updates,
                matched_handler_id=spec.id,
                matched_layer=spec.layer,
                trace=tuple(trace),
            )
            _log_engine_result(result)
            return result

    logger.info("gate_dispatch_to_planner", reason="planner_owned_or_unresolved_route")
    fallback_resolution = planner_handoff(ctx)
    fallback_updates = fallback_resolution.materialize()
    fallback_id = "planner_fallback"
    trace.append(
        GateTraceEntry(
            handler_id=fallback_id,
            layer=GateLayer.PLANNER_FALLBACK,
            duration_ms=0.0,
            matched=True,
            executed=True,
            gate_updates_changed=False,
            turn_directive=_extract_directive(fallback_updates),
        )
    )
    result = GateEngineResult(
        updates=fallback_updates,
        matched_handler_id=fallback_id,
        matched_layer=GateLayer.PLANNER_FALLBACK,
        trace=tuple(trace),
    )
    _log_engine_result(result)
    return result


__all__ = ["ordered_gate_handlers", "run_gate_engine"]
