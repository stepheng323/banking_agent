"""Log-only trace summaries for the layered gate engine."""

from __future__ import annotations

from apps.chat.src.agent.orchestrator.workflows.gate.contracts import GateEngineResult, GateTraceEntry


def _summarize_nested_entry(entry: GateTraceEntry) -> dict[str, object]:
    return {
        "handler_id": entry.handler_id,
        "layer": entry.layer.value,
        "routing_owner": entry.routing_owner,
        "routing_decision": entry.routing_decision,
    }


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
    family_traces: list[dict[str, object]] = [
        {
            "handler_id": entry.handler_id,
            "layer": entry.layer.value,
            "entry_count": len(entry.nested_trace),
            "matched_entries": [
                _summarize_nested_entry(nested_entry) for nested_entry in entry.nested_trace if nested_entry.matched
            ],
            "skipped_count": sum(1 for nested_entry in entry.nested_trace if not nested_entry.executed),
        }
        for entry in result.trace
        if entry.nested_trace
    ]
    return {
        "matched_handler_id": result.matched_handler_id,
        "matched_layer": result.matched_layer.value,
        "routing_owner": result.updates.get("routing_owner"),
        "routing_decision": result.updates.get("routing_decision"),
        "executed_handler_ids": executed_handler_ids,
        "skipped_handler_count": len(skipped_handlers),
        "skipped_handlers": skipped_handlers[:5],
        "family_traces": family_traces,
    }


__all__ = ["summarize_gate_trace"]
