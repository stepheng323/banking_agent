"""Protocols for Orchestrator Graph components.

Subgraphs are ephemeral workers that receive scoped input and return typed results.
They never call interrupt directly or maintain competing durable state.
"""

from typing import Any, Protocol, runtime_checkable

from apps.core.src.agent.orchestrator.state import OrchestratorState
from apps.core.src.agent.orchestrator.subgraph_types import SubgraphMode


@runtime_checkable
class SubgraphAdapter(Protocol):
    """Generic adapter protocol for orchestrator → subgraph communication.

    Adapters translate between orchestrator state and subgraph input/output.
    """

    async def invoke(self, task_id: str, mode: SubgraphMode, state: OrchestratorState) -> Any:
        """Invoke the subgraph for a task in the specified mode.

        The adapter:
        1. Builds scoped input from orchestrator state
        2. Calls the underlying service
        3. Returns the typed result

        Orchestrator applies patches and decides interrupts.
        """
        ...
