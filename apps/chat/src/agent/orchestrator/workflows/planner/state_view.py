"""Typed state access for planner workflow entry points."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from banking.presentation.i18n.locale import LocaleManager


@dataclass(frozen=True)
class PlannerStateView:
    """Read-only facade over planner-entry persisted orchestrator state."""

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
    def last_message_id(self) -> str | None:
        return self.state.last_message_id

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
    def has_waves(self) -> bool:
        return bool(self.state.waves)

    @property
    def has_pending_interrupt(self) -> bool:
        return self.state.pending_interrupt is not None

    @property
    def pending_interrupt_kind(self) -> str | None:
        pending_interrupt = self.state.pending_interrupt
        return pending_interrupt.kind if pending_interrupt is not None else None

    @property
    def has_session_stack(self) -> bool:
        return bool(self.state.session_stack)

    @property
    def stashed_query_session(self) -> dict[str, Any] | None:
        return self.state.stashed_query_session


def planner_state_view(state: OrchestratorState) -> PlannerStateView:
    return PlannerStateView(state)


__all__ = ["PlannerStateView", "planner_state_view"]
