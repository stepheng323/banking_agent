"""Worker lookup helpers for execution handlers."""

from __future__ import annotations

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.workflows.services import OrchestrationServices, WorkerName
from banking.runtime.protocols import WorkerProtocol


def _get_worker(
    services: OrchestrationServices,
    name: WorkerName,
    task: TaskSpec,
    *,
    log_key: str,
    error_message: str,
) -> WorkerProtocol | None:
    return services.require(name, task, log_key=log_key, error_message=error_message)


__all__ = ["_get_worker"]
