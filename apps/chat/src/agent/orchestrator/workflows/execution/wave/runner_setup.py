from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext
from apps.chat.src.agent.orchestrator.workflows.execution.dependencies import ExecutionDependencies
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
    locale: str
    mandate_gate_accounts: list[dict[str, Any]]


def _prepare_transaction_account_view(state: OrchestratorState) -> list[dict[str, Any]]:
    if not state.loaded_context or "accounts" not in state.loaded_context:
        return []

    raw_accounts = state.loaded_context["accounts"]
    mandate_gate_accounts = [account for account in raw_accounts if isinstance(account, dict)]
    logger.info(
        "mandate_gate_pre_filter",
        account_statuses=[
            {"bank": account.get("bank_name"), "mandate_status": account.get("mandate_status")}
            for account in raw_accounts
            if isinstance(account, dict)
        ],
    )
    state.loaded_context["transaction_accounts"] = [
        account for account in mandate_gate_accounts if is_mandate_debit_ready(account)
    ]
    logger.info(
        "mandate_gate_post_filter",
        ready_count=len(state.loaded_context["transaction_accounts"]),
    )
    return mandate_gate_accounts


def build_execution_wave_runtime(
    *,
    state: OrchestratorState,
    config: RunnableConfig,
    current_wave: list[str],
) -> ExecutionWaveRuntime:
    configurable = config.get("configurable", {})
    if not isinstance(configurable, Mapping):
        configurable = {}
    raw_services = cast(Mapping[str, object] | None, configurable.get("services"))
    services = OrchestrationServices.from_mapping(raw_services)
    dependencies = ExecutionDependencies.from_configurable(configurable)
    accumulator = ExecutionAccumulator(state.tasks)
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
        locale=(state.loaded_context or {}).get("language", "en"),
        mandate_gate_accounts=_prepare_transaction_account_view(state),
    )


__all__ = ["ExecutionWaveRuntime", "build_execution_wave_runtime"]
