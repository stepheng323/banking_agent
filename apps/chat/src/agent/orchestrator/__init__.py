"""Public orchestrator entrypoints."""

from importlib import import_module
from typing import Any

_PUBLIC_EXPORTS = {
    "OrchestratorAgent": ("apps.chat.src.agent.orchestrator.agent", "OrchestratorAgent"),
    "build_orchestrator_graph": (
        "apps.chat.src.agent.orchestrator.graph.builder",
        "build_orchestrator_graph",
    ),
}

__all__ = ["OrchestratorAgent", "build_orchestrator_graph"]


def __getattr__(name: str) -> Any:
    if name not in _PUBLIC_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attribute_name = _PUBLIC_EXPORTS[name]
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value
