"""Typed state access for interrupt workflow entry points."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.chat.src.agent.orchestrator.guardrails.interrupt_shortcuts import resolve_shortcut_locale
from apps.chat.src.agent.orchestrator.models.domain import ActiveSession, PendingInterrupt, TaskSpec
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.models import LocaleCode


@dataclass(frozen=True)
class InterruptStateView:
    """Read-only facade over interrupt-relevant persisted orchestrator state."""

    state: OrchestratorState

    @property
    def pending_interrupt(self) -> PendingInterrupt | None:
        return self.state.pending_interrupt

    @property
    def has_pending_interrupt(self) -> bool:
        return self.pending_interrupt is not None

    @property
    def last_message_text(self) -> str | None:
        return self.state.last_message_text

    @property
    def message_text(self) -> str:
        return self.last_message_text or ""

    @property
    def loaded_context(self) -> dict[str, Any]:
        return self.state.loaded_context

    @property
    def loaded_context_or_empty(self) -> dict[str, Any]:
        loaded_context = self.loaded_context
        return loaded_context if isinstance(loaded_context, dict) else {}

    @property
    def current_locale(self) -> str:
        return LocaleManager.normalize(self.loaded_context_or_empty.get("language")).value

    @property
    def shortcut_locale(self) -> LocaleCode | None:
        return resolve_shortcut_locale(self.loaded_context_or_empty.get("language"))

    @property
    def tasks(self) -> dict[str, TaskSpec]:
        return self.state.tasks

    @property
    def task_ids(self) -> set[str]:
        return set(self.tasks.keys())

    def task(self, task_id: str) -> TaskSpec | None:
        return self.tasks.get(task_id)

    def task_types_for(self, task_ids: list[str]) -> set[str]:
        return {task.type for task_id in task_ids if (task := self.task(task_id)) is not None}

    def has_task_id(self, task_id: str) -> bool:
        return task_id in self.task_ids

    @property
    def session_stack(self) -> list[ActiveSession]:
        return list(self.state.session_stack)

    def session_stack_without_domains(self, domains: set[str]) -> list[ActiveSession]:
        if not domains:
            return self.session_stack
        return [session for session in self.session_stack if session.domain not in domains]


def interrupt_state_view(state: OrchestratorState) -> InterruptStateView:
    return InterruptStateView(state)


__all__ = ["InterruptStateView", "interrupt_state_view"]
