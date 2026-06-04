"""Typed state access for the gate workflow."""

from __future__ import annotations

from dataclasses import dataclass

from apps.chat.src.agent.orchestrator.models.domain import ActiveSession, PendingInterrupt, TaskSpec
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState


@dataclass(frozen=True)
class GateStateView:
    """Read-only facade over gate-relevant persisted orchestrator state."""

    state: OrchestratorState

    @property
    def phone_number(self) -> str:
        return self.state.phone_number

    @property
    def last_message_text(self) -> str | None:
        return self.state.last_message_text

    @property
    def last_message_text_or_empty(self) -> str:
        return self.last_message_text or ""

    @property
    def message_text(self) -> str:
        return self.last_message_text_or_empty.strip()

    @property
    def has_quote(self) -> bool:
        return self.state.has_quote

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

    @property
    def pending_interrupt_task_ids(self) -> list[str]:
        interrupt = self.pending_interrupt
        if interrupt is None or not isinstance(getattr(interrupt, "task_ids", None), list):
            return []
        return [task_id for task_id in interrupt.task_ids if isinstance(task_id, str)]

    @property
    def pending_interrupt_task_types(self) -> set[str]:
        return {self.tasks[task_id].type for task_id in self.pending_interrupt_task_ids if task_id in self.tasks}

    @property
    def tasks(self) -> dict[str, TaskSpec]:
        return self.state.tasks

    @property
    def session_stack(self) -> list[ActiveSession]:
        return list(self.state.session_stack)

    @property
    def has_session_stack(self) -> bool:
        return bool(self.session_stack)

    @property
    def active_session(self) -> ActiveSession | None:
        stack = self.session_stack
        return stack[-1] if stack else None

    @property
    def active_session_domain(self) -> str | None:
        session = self.active_session
        return session.domain if session is not None else None

    @property
    def has_live_pending_interrupt(self) -> bool:
        interrupt = self.pending_interrupt
        if interrupt is None:
            return False

        task_types = self.pending_interrupt_task_types
        if not task_types:
            return False

        if interrupt.kind in {"confirmation", "auth"}:
            return True
        if interrupt.kind != "input":
            return True

        # Active query sessions own their own follow-up semantics and should not pay interrupt-router cost.
        return any(task_type != "query" for task_type in task_types)

    @property
    def active_domain(self) -> str | None:
        return self.state.active_domain

    def session_stack_without_domain(self, domain: str) -> list[ActiveSession]:
        return [session for session in self.session_stack if session.domain != domain]

    def has_session_for_domain(self, domain: str) -> bool:
        return any(session.domain == domain for session in self.session_stack)

    def is_numeric_input_interrupt_selection(self, message_text: str) -> bool:
        interrupt = self.pending_interrupt
        if interrupt is None or interrupt.kind != "input":
            return False
        if not message_text.strip().isdigit():
            return False

        task_ids = self.pending_interrupt_task_ids
        if len(task_ids) != 1:
            return False

        required_fields = interrupt.fields_by_task.get(task_ids[0]) or []
        return set(required_fields) == {"source_account_id"}


def gate_state_view(state: OrchestratorState) -> GateStateView:
    return GateStateView(state)


__all__ = ["GateStateView", "gate_state_view"]
