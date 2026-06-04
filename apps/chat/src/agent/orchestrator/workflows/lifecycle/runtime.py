"""Typed runtime construction for lifecycle workflow nodes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.lifecycle.state_view import (
    LifecycleStateView,
    lifecycle_state_view,
)
from banking.beneficiaries.services.post_transaction_beneficiary import BeneficiarySuggestionServiceProtocol


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
    state_view: LifecycleStateView
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

    state_view = lifecycle_state_view(state)
    return FinalizeRuntime(
        state=state,
        state_view=state_view,
        config=config,
        dependencies=LifecycleDependencies.from_configurable(configurable),
        outbox=state_view.outbox,
        locale=state_view.locale,
        completed_tasks=state_view.completed_tasks,
        failed_tasks=state_view.failed_tasks,
        cancelled_tasks=state_view.cancelled_tasks,
    )


__all__ = ["FinalizeRuntime", "LifecycleDependencies", "build_finalize_runtime"]
