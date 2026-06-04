"""Task executor registry for execution waves."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext
from apps.chat.src.agent.orchestrator.workflows.execution.executors.account_beneficiary import (
    AccountTaskExecutor,
    BeneficiaryTaskExecutor,
)
from apps.chat.src.agent.orchestrator.workflows.execution.executors.purchase import (
    AirtimeTaskExecutor,
    DataTaskExecutor,
)
from apps.chat.src.agent.orchestrator.workflows.execution.executors.query import QueryTaskExecutor
from apps.chat.src.agent.orchestrator.workflows.execution.executors.session import OrchestratorTaskExecutor
from apps.chat.src.agent.orchestrator.workflows.execution.executors.support import (
    FAQTaskExecutor,
    SupportTaskExecutor,
)
from apps.chat.src.agent.orchestrator.workflows.execution.executors.transfer import (
    ScheduleTaskExecutor,
    TransferTaskExecutor,
)


class TaskExecutor(Protocol):
    async def execute(self, task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
        """Execute one task and record state changes through the execution context."""


TaskExecutorRegistry = Mapping[str, TaskExecutor]

DEFAULT_TASK_EXECUTORS: TaskExecutorRegistry = {
    "transfer": TransferTaskExecutor(),
    "account": AccountTaskExecutor(),
    "beneficiary": BeneficiaryTaskExecutor(),
    "airtime": AirtimeTaskExecutor(),
    "query": QueryTaskExecutor(),
    "data": DataTaskExecutor(),
    "faq": FAQTaskExecutor(),
    "support": SupportTaskExecutor(),
    "schedule": ScheduleTaskExecutor(),
    "orchestrator": OrchestratorTaskExecutor(),
}


def get_task_executor(task_type: str, registry: TaskExecutorRegistry = DEFAULT_TASK_EXECUTORS) -> TaskExecutor | None:
    return registry.get(task_type)


__all__ = [
    "AccountTaskExecutor",
    "AirtimeTaskExecutor",
    "BeneficiaryTaskExecutor",
    "DataTaskExecutor",
    "DEFAULT_TASK_EXECUTORS",
    "FAQTaskExecutor",
    "OrchestratorTaskExecutor",
    "QueryTaskExecutor",
    "ScheduleTaskExecutor",
    "SupportTaskExecutor",
    "TaskExecutor",
    "TaskExecutorRegistry",
    "TransferTaskExecutor",
    "get_task_executor",
]
