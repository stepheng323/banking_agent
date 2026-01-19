"""Protocols for Orchestrator Graph components."""

from typing import Any, Protocol, runtime_checkable

from apps.core.src.agent.orchestrator_graph.state import OrchestratorState
from apps.core.src.agent.workflow.models import TaskResult
from shared.types.planner import PlannedTask


@runtime_checkable
class SubgraphAdapter(Protocol):
    """Protocol for domain-specific subgraphs/executors."""

    async def prepare(self, task: PlannedTask, state: OrchestratorState) -> dict[str, Any]:
        """
        Check readiness and identify missing fields.
        
        Returns:
            dict containing:
            - ready: bool
            - missing_fields: list[str]
            - updated_params: dict[str, Any] (optional hydration)
        """
        ...

    async def execute(self, task: PlannedTask, state: OrchestratorState) -> TaskResult:
        """
        Execute the task.
        Assumes authorization has been satisfied if required.
        """
        ...
