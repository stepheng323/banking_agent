"""Typed worker service registry shared by orchestration workflows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from banking.runtime.protocols import WorkerProtocol
from shared.utils.logging import get_logger

logger = get_logger(__name__)

WorkerName = Literal[
    "transfer",
    "account",
    "beneficiary",
    "airtime",
    "query",
    "data",
    "faq",
    "support",
]

_WORKER_NAMES: tuple[WorkerName, ...] = (
    "transfer",
    "account",
    "beneficiary",
    "airtime",
    "query",
    "data",
    "faq",
    "support",
)

WorkerService = object


@dataclass(frozen=True)
class OrchestrationServices:
    """Typed worker registry available to orchestration workflow nodes."""

    transfer: WorkerService | None = None
    account: WorkerService | None = None
    beneficiary: WorkerService | None = None
    airtime: WorkerService | None = None
    query: WorkerService | None = None
    data: WorkerService | None = None
    faq: WorkerService | None = None
    support: WorkerService | None = None

    @classmethod
    def empty(cls) -> OrchestrationServices:
        return cls()

    @classmethod
    def from_mapping(
        cls,
        services: Mapping[str, object] | OrchestrationServices | None,
    ) -> OrchestrationServices:
        if isinstance(services, OrchestrationServices):
            return services

        return cls(
            transfer=services.get("transfer") if services is not None else None,
            account=services.get("account") if services is not None else None,
            beneficiary=services.get("beneficiary") if services is not None else None,
            airtime=services.get("airtime") if services is not None else None,
            query=services.get("query") if services is not None else None,
            data=services.get("data") if services is not None else None,
            faq=services.get("faq") if services is not None else None,
            support=services.get("support") if services is not None else None,
        )

    def get(self, name: WorkerName | str) -> WorkerService | None:
        if name not in _WORKER_NAMES:
            return None
        return getattr(self, name)

    def require(
        self,
        name: WorkerName,
        task: TaskSpec,
        *,
        log_key: str,
        error_message: str,
    ) -> WorkerProtocol | None:
        candidate = self.get(name)
        if isinstance(candidate, WorkerProtocol):
            return candidate
        if candidate is not None:
            logger.warning(
                "orchestration_service_ignored_invalid_worker",
                worker=name,
                worker_type=type(candidate).__name__,
            )
        logger.error(log_key)
        task.stage = TaskStage.FAILED
        task.payload["error"] = error_message
        return None


__all__ = ["OrchestrationServices", "WorkerName", "WorkerService"]
