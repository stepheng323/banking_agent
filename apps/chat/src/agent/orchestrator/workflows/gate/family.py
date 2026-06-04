"""Family routers for grouped gate handlers."""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping

from apps.chat.src.agent.orchestrator.workflows.gate.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.contracts import (
    GATE_LAYER_INDEX,
    GateEligibilityResult,
    GateHandler,
    GateHandlerSpec,
    GateTraceEntry,
    GateUpdates,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _ordered_subhandlers(handlers: Iterable[GateHandlerSpec]) -> tuple[GateHandlerSpec, ...]:
    return tuple(sorted(handlers, key=lambda spec: (GATE_LAYER_INDEX[spec.layer], spec.priority)))


def _default_eligibility() -> GateEligibilityResult:
    return GateEligibilityResult(eligible=True, reason="default_eligible", details={})


def _routing_value(updates: Mapping[str, object], key: str) -> str | None:
    value = updates.get(key)
    return value if isinstance(value, str) and value else None


def _trace_entry(
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
        routing_owner=_routing_value(routing_updates, "routing_owner"),
        routing_decision=_routing_value(routing_updates, "routing_decision"),
        skip_reason=skip_reason,
        skip_details=skip_details,
    )


def build_gate_family_handler(family_id: str, subhandlers: Iterable[GateHandlerSpec]) -> GateHandler:
    """Build a handler that runs ordered subhandlers and records nested trace."""
    ordered_subhandlers = _ordered_subhandlers(subhandlers)

    async def _run_family(ctx: GateContext) -> GateUpdates | None:
        trace: list[GateTraceEntry] = []
        for spec in ordered_subhandlers:
            before_updates = dict(ctx.gate_updates)
            eligibility_start = time.perf_counter()
            try:
                eligibility = spec.eligibility(ctx) if spec.eligibility is not None else _default_eligibility()
            except Exception:
                logger.exception("gate_family_eligibility_failed", family_id=family_id, handler_id=spec.id)
                raise
            eligibility_duration_ms = (time.perf_counter() - eligibility_start) * 1000
            if not eligibility.eligible:
                trace.append(
                    _trace_entry(
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
                logger.exception("gate_family_handler_failed", family_id=family_id, handler_id=spec.id)
                raise
            duration_ms = (time.perf_counter() - start) * 1000
            matched = updates is not None
            trace.append(
                _trace_entry(
                    spec,
                    duration_ms=duration_ms,
                    matched=matched,
                    executed=True,
                    gate_updates_changed=ctx.gate_updates != before_updates,
                    routing_updates=updates if updates is not None else ctx.gate_updates,
                )
            )
            if updates is not None:
                ctx.nested_gate_traces[family_id] = tuple(trace)
                return updates

        ctx.nested_gate_traces[family_id] = tuple(trace)
        return None

    _run_family.__name__ = f"_gate_family_{family_id}"
    return _run_family


__all__ = ["build_gate_family_handler"]
