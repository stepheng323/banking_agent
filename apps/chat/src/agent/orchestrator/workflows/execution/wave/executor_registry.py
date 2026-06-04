"""Task executor registry for execution waves."""

from __future__ import annotations

from collections.abc import Mapping
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


class TaskExecutor(Protocol):
    async def execute(self, task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
        """Execute one task and record state changes through the execution context."""


class TransferTaskExecutor:
    async def execute(self, task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
        await handle_transfer_task(task, task_id, ctx)


class AccountTaskExecutor:
    async def execute(self, task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
        await handle_account_task(task, task_id, ctx)


class BeneficiaryTaskExecutor:
    async def execute(self, task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
        await handle_beneficiary_task(task, task_id, ctx)


class AirtimeTaskExecutor:
    async def execute(self, task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
        await handle_airtime_task(task, task_id, ctx)


class QueryTaskExecutor:
    async def execute(self, task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
        await handle_query_task(task, task_id, ctx)


class DataTaskExecutor:
    async def execute(self, task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
        await handle_data_task(task, task_id, ctx)


class FAQTaskExecutor:
    async def execute(self, task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
        await handle_faq_task(task, task_id, ctx)


class SupportTaskExecutor:
    async def execute(self, task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
        await handle_support_task(task, task_id, ctx)


class ScheduleTaskExecutor:
    async def execute(self, task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
        await handle_schedule_task(task, task_id, ctx)


class OrchestratorTaskExecutor:
    async def execute(self, task: TaskSpec, task_id: str, ctx: ExecutionTurnContext) -> None:
        await handle_orchestrator_task(task, task_id, ctx)


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
