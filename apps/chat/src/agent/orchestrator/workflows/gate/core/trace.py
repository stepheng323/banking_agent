"""Log-only trace summaries for the layered gate engine."""

from __future__ import annotations

from apps.chat.src.agent.orchestrator.models.turn_directive import TurnDirective
from apps.chat.src.agent.orchestrator.workflows.gate.core.contracts import GateEngineResult


def summarize_gate_trace(result: GateEngineResult) -> dict[str, object]:
    executed_handler_ids = [entry.handler_id for entry in result.trace if entry.executed]
    skipped_handlers: list[dict[str, object]] = [
        {
            "handler_id": entry.handler_id,
            "layer": entry.layer.value,
            "reason": entry.skip_reason or "ineligible",
            "details": entry.skip_details or {},
        }
        for entry in result.trace
        if not entry.executed
    ]
    directive = result.updates.get("turn_directive")
    routing_owner = directive.owner if isinstance(directive, TurnDirective) else None
    routing_decision = directive.decision if isinstance(directive, TurnDirective) else None
    return {
        "matched_handler_id": result.matched_handler_id,
        "matched_layer": result.matched_layer.value,
        "routing_owner": routing_owner,
        "routing_decision": routing_decision,
        "executed_handler_ids": executed_handler_ids,
        "skipped_handler_count": len(skipped_handlers),
        "skipped_handlers": skipped_handlers[:5],
    }


__all__ = ["summarize_gate_trace"]
