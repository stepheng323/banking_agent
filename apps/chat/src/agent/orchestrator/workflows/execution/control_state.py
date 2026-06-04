"""Typed execution control-state access."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import PendingInterrupt
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState


@dataclass(frozen=True)
class ExecutionControlState:
    """Read-only facade over execution control fields."""

    state: OrchestratorState

    @property
    def pending_interrupt(self) -> PendingInterrupt | None:
        return self.state.pending_interrupt

    @property
    def has_pending_interrupt(self) -> bool:
        return self.pending_interrupt is not None

    @property
    def pending_interrupt_kind(self) -> str | None:
        interrupt = self.pending_interrupt
        return interrupt.kind if interrupt is not None else None

    def pending_interrupt_update(self) -> dict[str, PendingInterrupt]:
        interrupt = self.pending_interrupt
        if interrupt is None:
            return {}
        return {"pending_interrupt": interrupt}

    @property
    def policy_notice(self) -> str | None:
        return self.state.policy_notice or None

    @property
    def has_policy_notice(self) -> bool:
        return self.policy_notice is not None

    def prepend_policy_notice(self, outbox: list[dict[str, Any]]) -> list[dict[str, Any]]:
        notice = self.policy_notice
        if notice is None:
            return outbox
        return [{"type": "say", "text": notice}, *outbox]


def execution_control_state(state: OrchestratorState) -> ExecutionControlState:
    return ExecutionControlState(state)


__all__ = ["ExecutionControlState", "execution_control_state"]
