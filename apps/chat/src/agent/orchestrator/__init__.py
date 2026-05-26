"""Orchestrator package exports.

Keep imports lazy so lightweight runtimes can import orchestrator submodules
without pulling chat-only dependencies at module import time.
"""

from typing import TYPE_CHECKING, Any

__all__ = [
    "OrchestratorAgent",
]

if TYPE_CHECKING:
    from apps.chat.src.agent.orchestrator.graph.orchestrator import OrchestratorAgent


def __getattr__(name: str) -> Any:
    if name == "OrchestratorAgent":
        from apps.chat.src.agent.orchestrator.graph.orchestrator import OrchestratorAgent

        return OrchestratorAgent

    raise AttributeError(f"module 'apps.chat.src.agent.orchestrator' has no attribute '{name}'")
