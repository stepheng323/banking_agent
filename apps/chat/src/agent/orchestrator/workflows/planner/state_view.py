"""Typed state access for planner workflow entry points."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from apps.chat.src.agent.orchestrator.context.models import ContextFrame
from apps.chat.src.agent.orchestrator.models.domain import PendingInterrupt, TaskSpec
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from banking.presentation.i18n.locale import LocaleManager
from shared.types.planner import PlannerOutput, TransactionExecutor

_TRANSACTION_EXECUTOR_VALUES = frozenset({"transfer", "airtime", "data"})


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
    def has_quote(self) -> bool:
        return self.state.has_quote

    @property
    def quoted_message_id(self) -> str | None:
        return self.state.quoted_message_id

    @property
    def has_quoted_message(self) -> bool:
        return self.has_quote and bool(self.quoted_message_id)

    @property
    def planner_output(self) -> PlannerOutput | None:
        return self.state.planner_output

    @property
    def tasks(self) -> dict[str, TaskSpec]:
        return self.state.tasks

    @property
    def waves(self) -> list[list[str]]:
        return self.state.waves

    @property
    def current_wave_index(self) -> int:
        return self.state.current_wave_index

    @property
    def current_wave_task_ids(self) -> list[str]:
        if not self.waves or self.current_wave_index >= len(self.waves):
            return []
        return list(self.waves[self.current_wave_index])

    @property
    def current_wave_first_task_type(self) -> str | None:
        task_ids = self.current_wave_task_ids
        if not task_ids:
            return None
        task = self.tasks.get(task_ids[0])
        return task.type if task else None

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
    def pending_interrupt(self) -> PendingInterrupt | None:
        return self.state.pending_interrupt

    @property
    def has_pending_interrupt(self) -> bool:
        return self.pending_interrupt is not None

    @property
    def pending_interrupt_kind(self) -> str | None:
        pending_interrupt = self.pending_interrupt
        return pending_interrupt.kind if pending_interrupt is not None else None

    @property
    def pending_interrupt_task_ids(self) -> list[str]:
        pending_interrupt = self.pending_interrupt
        if pending_interrupt is None:
            return []
        return [task_id for task_id in pending_interrupt.task_ids if isinstance(task_id, str)]

    @property
    def pending_interrupt_task_types(self) -> set[str]:
        return {self.tasks[task_id].type for task_id in self.pending_interrupt_task_ids if task_id in self.tasks}

    @property
    def has_session_stack(self) -> bool:
        return bool(self.state.session_stack)

    @property
    def context_frames(self) -> list[ContextFrame]:
        return list(self.state.context_frames)

    @property
    def direct_path_triggered(self) -> bool:
        return self.state.direct_path_triggered

    @property
    def routing_owner(self) -> str | None:
        return self.state.routing_owner

    @property
    def routing_decision(self) -> str | None:
        return self.state.routing_decision

    @property
    def routing_target_domain(self) -> str | None:
        return self.state.routing_target_domain

    @property
    def raw_expected_transaction_executors(self) -> tuple[str, ...]:
        return tuple(self.state.preplanner_expected_transaction_executors)

    @property
    def expected_transaction_executors(self) -> tuple[TransactionExecutor, ...]:
        return tuple(
            cast(TransactionExecutor, item)
            for item in self.raw_expected_transaction_executors
            if item in _TRANSACTION_EXECUTOR_VALUES
        )

    @property
    def has_transfer_only_preplanner_expectation(self) -> bool:
        return self.raw_expected_transaction_executors == ("transfer",)

    @property
    def stashed_query_session(self) -> dict[str, Any] | None:
        return self.state.stashed_query_session


def planner_state_view(state: OrchestratorState) -> PlannerStateView:
    return PlannerStateView(state)


__all__ = ["PlannerStateView", "planner_state_view"]
