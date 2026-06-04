"""Typed runtime construction for the planner workflow."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from banking.presentation.i18n.locale import LocaleManager


@dataclass(frozen=True)
class PlannerDependencies:
    """Runtime dependencies available to planner workflow helpers."""

    task_planner: Any | None
    redis_client: Any | None
    conversation_responder: Any | None
    actionable_message_repo: Any | None

    @classmethod
    def from_configurable(cls, configurable: Mapping[str, Any]) -> PlannerDependencies:
        return cls(
            task_planner=configurable.get("task_planner"),
            redis_client=configurable.get("redis_client"),
            conversation_responder=configurable.get("conversation_responder"),
            actionable_message_repo=configurable.get("actionable_message_repo"),
        )


@dataclass(frozen=True)
class PlannerRuntime:
    """Typed state derived once at the LangGraph planner node boundary."""

    state: OrchestratorState
    config: RunnableConfig
    dependencies: PlannerDependencies
    text: str
    current_locale: str


def build_planner_runtime(state: OrchestratorState, config: RunnableConfig) -> PlannerRuntime:
    configurable = config.get("configurable", {})
    if not isinstance(configurable, Mapping):
        configurable = {}

    return PlannerRuntime(
        state=state,
        config=config,
        dependencies=PlannerDependencies.from_configurable(configurable),
        text=state.last_message_text or "",
        current_locale=LocaleManager.normalize(state.loaded_context.get("language")).value,
    )


__all__ = ["PlannerDependencies", "PlannerRuntime", "build_planner_runtime"]
