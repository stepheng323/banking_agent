"""Orchestrator package exports.

Keep imports lazy so lightweight runtimes can import orchestrator submodules
without pulling chat-only dependencies at module import time.
"""

from typing import TYPE_CHECKING, Any

__all__ = [
    "OrchestratorAgent",
    "OrchestratorContextManager",
    "OrchestratorTaskPlanner",
    "ConversationResponder",
]

if TYPE_CHECKING:
    from apps.core.src.agent.orchestrator.graph.orchestrator import OrchestratorAgent
    from shared.services.context_manager import OrchestratorContextManager
    from shared.services.conversation_responder import ConversationResponder
    from shared.services.task_planner import OrchestratorTaskPlanner


def __getattr__(name: str) -> Any:
    if name == "OrchestratorAgent":
        from apps.core.src.agent.orchestrator.graph.orchestrator import OrchestratorAgent

        return OrchestratorAgent

    if name == "OrchestratorContextManager":
        from shared.services.context_manager import OrchestratorContextManager

        return OrchestratorContextManager

    if name == "OrchestratorTaskPlanner":
        from shared.services.task_planner import OrchestratorTaskPlanner

        return OrchestratorTaskPlanner

    if name == "ConversationResponder":
        from shared.services.conversation_responder import ConversationResponder

        return ConversationResponder

    raise AttributeError(f"module 'apps.core.src.agent.orchestrator' has no attribute '{name}'")
