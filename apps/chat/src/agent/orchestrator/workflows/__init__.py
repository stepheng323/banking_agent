"""Public workflow entrypoints used by the orchestrator graph."""

from importlib import import_module
from typing import Any

_GRAPH_ENTRYPOINTS = {
    "advance_wave": ("apps.chat.src.agent.orchestrator.workflows.execution.node", "advance_wave"),
    "finalize": ("apps.chat.src.agent.orchestrator.workflows.lifecycle.finalize", "finalize"),
    "handle_pending_interrupt": (
        "apps.chat.src.agent.orchestrator.workflows.interrupt.node",
        "handle_pending_interrupt",
    ),
    "ingest_message": ("apps.chat.src.agent.orchestrator.workflows.lifecycle.ingest", "ingest_message"),
    "plan_tasks": ("apps.chat.src.agent.orchestrator.workflows.planner.node", "plan_tasks"),
    "session_gate_direct_path": (
        "apps.chat.src.agent.orchestrator.workflows.gate.node",
        "session_gate_direct_path",
    ),
}

__all__ = [
    "advance_wave",
    "finalize",
    "handle_pending_interrupt",
    "ingest_message",
    "plan_tasks",
    "session_gate_direct_path",
]


def __getattr__(name: str) -> Any:
    if name not in _GRAPH_ENTRYPOINTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attribute_name = _GRAPH_ENTRYPOINTS[name]
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value
