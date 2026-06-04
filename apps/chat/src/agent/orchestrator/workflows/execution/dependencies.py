"""Typed dependency extraction for execution handlers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.workflows.runtime_config import OrchestrationConfig


class BeneficiaryLookupRepositoryProtocol(Protocol):
    async def get_by_user(self, user_id: str, beneficiary_type: str | None = None) -> list[Any]: ...

    async def search_by_name(
        self,
        user_id: str,
        query: str,
        *,
        beneficiary_type: str | None = None,
    ) -> list[Any]: ...


class BeneficiarySuggestionSaverProtocol(Protocol):
    async def save_beneficiary(self, phone_number: str, alias: Any = None, locale: str = "en") -> str: ...


class ReceiptPublisherProtocol(Protocol):
    async def publish(self, topic: str, payload: dict[str, Any]) -> Any: ...


@dataclass(frozen=True)
class ExecutionDependencies:
    """Runtime dependencies available to execution handlers."""

    beneficiary_repo: BeneficiaryLookupRepositoryProtocol | None
    beneficiary_suggestion_service: BeneficiarySuggestionSaverProtocol | None
    publisher: ReceiptPublisherProtocol | None
    progress_tracker: Any | None

    @classmethod
    def empty(cls) -> ExecutionDependencies:
        return cls(
            beneficiary_repo=None,
            beneficiary_suggestion_service=None,
            publisher=None,
            progress_tracker=None,
        )

    @classmethod
    def from_configurable(cls, configurable: Mapping[str, Any]) -> ExecutionDependencies:
        return cls(
            beneficiary_repo=cast(
                BeneficiaryLookupRepositoryProtocol | None,
                configurable.get("beneficiary_repo"),
            ),
            beneficiary_suggestion_service=cast(
                BeneficiarySuggestionSaverProtocol | None,
                configurable.get("beneficiary_suggestion_service"),
            ),
            publisher=cast(ReceiptPublisherProtocol | None, configurable.get("publisher")),
            progress_tracker=configurable.get("progress_tracker"),
        )

    @classmethod
    def from_config(cls, config: RunnableConfig) -> ExecutionDependencies:
        runtime_config = OrchestrationConfig.from_runnable_config(config)
        return cls.from_configurable(runtime_config.configurable)


__all__ = [
    "BeneficiaryLookupRepositoryProtocol",
    "BeneficiarySuggestionSaverProtocol",
    "ExecutionDependencies",
    "ReceiptPublisherProtocol",
]
