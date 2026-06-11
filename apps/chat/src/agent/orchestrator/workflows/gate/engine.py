"""Layered routing engine for the orchestrator gate."""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping

from apps.chat.src.agent.orchestrator.workflows.gate.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.contracts import (
    GATE_LAYER_INDEX,
    GateEligibilityResult,
    GateEngineResult,
    GateHandlerSpec,
    GateLayer,
    GateTraceEntry,
)
from apps.chat.src.agent.orchestrator.workflows.gate.outcomes import planner_handoff
from apps.chat.src.agent.orchestrator.workflows.gate.trace import summarize_gate_trace
from shared.utils.logging import get_logger, log_orchestrator_diagnostic

logger = get_logger(__name__)


def ordered_gate_handlers(handlers: Iterable[GateHandlerSpec]) -> tuple[GateHandlerSpec, ...]:
    """Return handlers in deterministic layered execution order."""
    return tuple(sorted(handlers, key=lambda spec: (GATE_LAYER_INDEX[spec.layer], spec.priority)))


def _routing_value(updates: Mapping[str, object], key: str) -> str | None:
    value = updates.get(key)
    return value if isinstance(value, str) and value else None


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
    nested_trace: tuple[GateTraceEntry, ...] = (),
) -> GateTraceEntry:
    return GateTraceEntry(
        handler_id=spec.id,
        layer=spec.layer,
        duration_ms=duration_ms,
        matched=matched,
        executed=executed,
        gate_updates_changed=gate_updates_changed,
        routing_owner=_routing_value(routing_updates, "routing_owner"),
        routing_decision=_routing_value(routing_updates, "routing_decision"),
        skip_reason=skip_reason,
        skip_details=skip_details,
        nested_trace=nested_trace,
    )


def _default_eligibility() -> GateEligibilityResult:
    return GateEligibilityResult(eligible=True, reason="default_eligible", details={})


def _pop_nested_trace(ctx: GateContext, handler_id: str) -> tuple[GateTraceEntry, ...]:
    entries = ctx.nested_gate_traces.pop(handler_id, ())
    if not entries:
        return ()
    validated_entries: list[GateTraceEntry] = []
    for entry in entries:
        if not isinstance(entry, GateTraceEntry):
            logger.warning("gate_engine_nested_trace_ignored", handler_id=handler_id)
            return ()
        validated_entries.append(entry)
    return tuple(validated_entries)


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
        nested_trace = _pop_nested_trace(ctx, spec.id)
        trace.append(
            _build_trace_entry(
                spec,
                duration_ms=duration_ms,
                matched=matched,
                executed=True,
                gate_updates_changed=gate_updates_changed,
                routing_updates=routing_updates,
                nested_trace=nested_trace,
            )
        )

        if updates is not None:
            result = GateEngineResult(
                updates=updates,
                matched_handler_id=spec.id,
                matched_layer=spec.layer,
                trace=tuple(trace),
            )
            _log_engine_result(result)
            return result

    logger.info("gate_dispatch_to_planner", reason="planner_owned_or_unresolved_route")
    fallback_updates = planner_handoff(ctx)
    trace.append(
        GateTraceEntry(
            handler_id="planner_fallback",
            layer=GateLayer.PLANNER_FALLBACK,
            duration_ms=0.0,
            matched=True,
            executed=True,
            gate_updates_changed=False,
            routing_owner=_routing_value(fallback_updates, "routing_owner"),
            routing_decision=_routing_value(fallback_updates, "routing_decision"),
        )
    )
    result = GateEngineResult(
        updates=fallback_updates,
        matched_handler_id="planner_fallback",
        matched_layer=GateLayer.PLANNER_FALLBACK,
        trace=tuple(trace),
    )
    _log_engine_result(result)
    return result


__all__ = ["ordered_gate_handlers", "run_gate_engine"]
