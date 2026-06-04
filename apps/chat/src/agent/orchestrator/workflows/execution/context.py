"""Typed context passed to execution task handlers."""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.dependencies import ExecutionDependencies
from apps.chat.src.agent.orchestrator.workflows.services import OrchestrationServices, WorkerName
from banking.runtime.protocols import WorkerProtocol


@dataclass
class ExecutionTurnContext:
    state: OrchestratorState
    config: RunnableConfig
    services: OrchestrationServices
    current_wave_len: int
    accumulator: ExecutionAccumulator
    current_wave_task_ids: list[str] | None = None
    execution_dependencies: ExecutionDependencies | None = None

    @property
    def dependencies(self) -> ExecutionDependencies:
        if self.execution_dependencies is None:
            self.execution_dependencies = ExecutionDependencies.from_config(self.config)
        return self.execution_dependencies

    def require_worker(
        self,
        name: WorkerName,
        task: TaskSpec,
        *,
        log_key: str,
        error_message: str,
    ) -> WorkerProtocol | None:
        return self.services.require(name, task, log_key=log_key, error_message=error_message)


__all__ = ["ExecutionTurnContext"]
