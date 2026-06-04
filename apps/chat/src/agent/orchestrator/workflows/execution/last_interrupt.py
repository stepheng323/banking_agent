"""Typed last-interrupt access for execution orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from apps.chat.src.agent.orchestrator.models.domain import PendingInterrupt
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState

InterruptKind = Literal["input", "confirmation", "auth"]


@dataclass(frozen=True)
class ExecutionLastInterrupt:
    """Read-only typed facade over the graph state's previous interrupt."""

    interrupt: PendingInterrupt | None

    @property
    def exists(self) -> bool:
        return self.interrupt is not None

    @property
    def kind(self) -> InterruptKind | None:
        return self.interrupt.kind if self.interrupt else None

    @property
    def task_ids(self) -> list[str]:
        return list(self.interrupt.task_ids) if self.interrupt else []

    @property
    def prompt(self) -> str | None:
        return self.interrupt.prompt if self.interrupt else None

    @property
    def task_count(self) -> int:
        return len(self.task_ids)

    def is_kind(self, kind: InterruptKind) -> bool:
        return self.interrupt is not None and self.interrupt.kind == kind

    def includes_task(self, task_id: str) -> bool:
        return self.interrupt is not None and task_id in self.interrupt.task_ids

    def fields_for_task(self, task_id: str) -> list[str]:
        if not self.interrupt:
            return []
        raw_fields = self.interrupt.fields_by_task.get(task_id, [])
        return [field for field in raw_fields if isinstance(field, str)]


def last_interrupt(state: OrchestratorState) -> ExecutionLastInterrupt:
    return ExecutionLastInterrupt(state.last_interrupt)


__all__ = ["ExecutionLastInterrupt", "InterruptKind", "last_interrupt"]
