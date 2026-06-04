"""Task executor registry for execution waves."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.task_handlers.account_beneficiary import (
    handle_account_task,
    handle_beneficiary_task,
)
from apps.chat.src.agent.orchestrator.task_handlers.purchase import (
    handle_airtime_task,
    handle_data_task,
)
from apps.chat.src.agent.orchestrator.task_handlers.query import handle_query_task
from apps.chat.src.agent.orchestrator.task_handlers.session import handle_orchestrator_task
from apps.chat.src.agent.orchestrator.task_handlers.support import (
    handle_faq_task,
    handle_support_task,
)
from apps.chat.src.agent.orchestrator.task_handlers.transfer import (
    handle_schedule_task,
    handle_transfer_task,
)
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext

TaskHandler = Callable[[TaskSpec, str, ExecutionTurnContext], Awaitable[None]]


class TaskExecutor(Protocol):
    async def execute(self, task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
        """Execute one task and record state changes through the execution context."""


@dataclass(frozen=True)
class FunctionTaskExecutor:
    handler: TaskHandler

    async def execute(self, task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
        await self.handler(task, task_id, ctx)


TaskExecutorRegistry = Mapping[str, TaskExecutor]

DEFAULT_TASK_EXECUTORS: TaskExecutorRegistry = {
    "transfer": FunctionTaskExecutor(handle_transfer_task),
    "account": FunctionTaskExecutor(handle_account_task),
    "beneficiary": FunctionTaskExecutor(handle_beneficiary_task),
    "airtime": FunctionTaskExecutor(handle_airtime_task),
    "query": FunctionTaskExecutor(handle_query_task),
    "data": FunctionTaskExecutor(handle_data_task),
    "faq": FunctionTaskExecutor(handle_faq_task),
    "support": FunctionTaskExecutor(handle_support_task),
    "schedule": FunctionTaskExecutor(handle_schedule_task),
    "orchestrator": FunctionTaskExecutor(handle_orchestrator_task),
}


def get_task_executor(task_type: str, registry: TaskExecutorRegistry = DEFAULT_TASK_EXECUTORS) -> TaskExecutor | None:
    return registry.get(task_type)


__all__ = [
    "DEFAULT_TASK_EXECUTORS",
    "FunctionTaskExecutor",
    "TaskExecutor",
    "TaskExecutorRegistry",
    "get_task_executor",
]
