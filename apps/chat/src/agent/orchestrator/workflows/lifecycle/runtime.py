"""Typed runtime construction for lifecycle workflow nodes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from banking.beneficiaries.services.post_transaction_beneficiary import BeneficiarySuggestionServiceProtocol
from banking.presentation.i18n.locale import LocaleManager


@dataclass(frozen=True)
class LifecycleDependencies:
    """Runtime dependencies used by lifecycle finalization."""

    beneficiary_suggestion_service: BeneficiarySuggestionServiceProtocol | None

    @classmethod
    def from_configurable(cls, configurable: Mapping[str, Any]) -> LifecycleDependencies:
        return cls(
            beneficiary_suggestion_service=cast(
                BeneficiarySuggestionServiceProtocol | None,
                configurable.get("beneficiary_suggestion_service"),
            )
        )


@dataclass(frozen=True)
class FinalizeRuntime:
    """Typed state derived once at the LangGraph finalize node boundary."""

    state: OrchestratorState
    config: RunnableConfig
    dependencies: LifecycleDependencies
    outbox: list[dict[str, Any]]
    locale: str
    completed_tasks: list[TaskSpec]
    failed_tasks: list[TaskSpec]
    cancelled_tasks: list[TaskSpec]


def build_finalize_runtime(state: OrchestratorState, config: RunnableConfig) -> FinalizeRuntime:
    configurable = config.get("configurable", {})
    if not isinstance(configurable, Mapping):
        configurable = {}

    return FinalizeRuntime(
        state=state,
        config=config,
        dependencies=LifecycleDependencies.from_configurable(configurable),
        outbox=list(state.outbox),
        locale=LocaleManager.normalize((state.loaded_context or {}).get("language")).value,
        completed_tasks=[task for task in state.tasks.values() if task.stage == TaskStage.COMPLETED],
        failed_tasks=[task for task in state.tasks.values() if task.stage == TaskStage.FAILED],
        cancelled_tasks=[task for task in state.tasks.values() if task.stage == TaskStage.CANCELLED],
    )


__all__ = ["FinalizeRuntime", "LifecycleDependencies", "build_finalize_runtime"]
