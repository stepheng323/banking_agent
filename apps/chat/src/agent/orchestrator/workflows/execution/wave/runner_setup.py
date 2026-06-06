from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext
from apps.chat.src.agent.orchestrator.workflows.execution.dependencies import ExecutionDependencies
from apps.chat.src.agent.orchestrator.workflows.execution.loaded_context import (
    loaded_context,
    set_loaded_context_value,
)
from apps.chat.src.agent.orchestrator.workflows.execution.task_access import task_map
from apps.chat.src.agent.orchestrator.workflows.execution.wave.executor_registry import (
    DEFAULT_TASK_EXECUTORS,
    TaskExecutorRegistry,
)
from apps.chat.src.agent.orchestrator.workflows.runtime_config import OrchestrationConfig
from apps.chat.src.agent.orchestrator.workflows.services import OrchestrationServices
from banking.accounts.mandate_state import is_mandate_debit_ready
from shared.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ExecutionWaveRuntime:
    current_wave: list[str]
    services: OrchestrationServices
    accumulator: ExecutionAccumulator
    ctx: ExecutionTurnContext
    task_executors: TaskExecutorRegistry
    locale: str
    mandate_gate_accounts: list[dict[str, Any]]


def _prepare_transaction_account_view(state: OrchestratorState) -> list[dict[str, Any]]:
    context = loaded_context(state)
    raw_accounts = context.accounts
    if not raw_accounts:
        return []

    mandate_gate_accounts = [account for account in raw_accounts if isinstance(account, dict)]
    logger.info(
        "mandate_gate_pre_filter",
        account_statuses=[
            {"bank": account.get("bank_name"), "mandate_status": account.get("mandate_status")}
            for account in raw_accounts
            if isinstance(account, dict)
        ],
    )
    transaction_accounts = [account for account in mandate_gate_accounts if is_mandate_debit_ready(account)]
    set_loaded_context_value(state, "transaction_accounts", transaction_accounts)
    logger.info(
        "mandate_gate_post_filter",
        ready_count=len(transaction_accounts),
    )
    return mandate_gate_accounts


def build_execution_wave_runtime(
    *,
    state: OrchestratorState,
    config: RunnableConfig,
    current_wave: list[str],
) -> ExecutionWaveRuntime:
    runtime_config = OrchestrationConfig.from_runnable_config(config)
    services = runtime_config.services()
    dependencies = ExecutionDependencies.from_configurable(runtime_config.configurable)
    accumulator = ExecutionAccumulator(task_map(state), initial_outbox=state.outbox)
    ctx = ExecutionTurnContext(
        state=state,
        config=config,
        services=services,
        current_wave_len=len(current_wave),
        current_wave_task_ids=list(current_wave),
        accumulator=accumulator,
        execution_dependencies=dependencies,
    )
    return ExecutionWaveRuntime(
        current_wave=current_wave,
        services=services,
        accumulator=accumulator,
        ctx=ctx,
        task_executors=DEFAULT_TASK_EXECUTORS,
        locale=loaded_context(state).value("language", "en"),
        mandate_gate_accounts=_prepare_transaction_account_view(state),
    )


__all__ = ["ExecutionWaveRuntime", "build_execution_wave_runtime"]
