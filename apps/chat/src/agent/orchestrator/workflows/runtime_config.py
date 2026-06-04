"""Typed access to LangGraph runtime configuration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.workflows.services import OrchestrationServices


@dataclass(frozen=True)
class OrchestrationConfig:
    """Normalized view of RunnableConfig configurable values."""

    configurable: Mapping[str, Any]

    @classmethod
    def from_runnable_config(cls, config: RunnableConfig) -> OrchestrationConfig:
        configurable = config.get("configurable", {})
        if not isinstance(configurable, Mapping):
            configurable = {}
        return cls(configurable=cast(Mapping[str, Any], configurable))

    def get(self, key: str) -> Any | None:
        return self.configurable.get(key)

    def services(self) -> OrchestrationServices:
        raw_services = cast(OrchestrationServices | Mapping[str, object] | None, self.get("services"))
        return OrchestrationServices.from_mapping(raw_services)


__all__ = ["OrchestrationConfig"]
