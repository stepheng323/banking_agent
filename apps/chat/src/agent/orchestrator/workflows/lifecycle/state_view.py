"""Typed state access for lifecycle workflow nodes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.chat.src.agent.orchestrator.context.models import ContextFrame
from apps.chat.src.agent.orchestrator.context.referents.models import ShortTermReferentMemory
from apps.chat.src.agent.orchestrator.models.domain import PendingInterrupt, TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from banking.presentation.i18n.locale import LocaleManager

_TERMINAL_TASK_STAGES = {TaskStage.COMPLETED, TaskStage.FAILED, TaskStage.CANCELLED}


@dataclass(frozen=True)
class LifecycleStateView:
    """Read-only facade over lifecycle-relevant persisted orchestrator state."""

    state: OrchestratorState

    @property
    def phone_number(self) -> str:
        return self.state.phone_number

    @property
    def channel(self) -> str:
        return self.state.channel

    @property
    def channel_identity(self) -> str | None:
        return self.state.channel_identity

    @property
    def last_message_text(self) -> str | None:
        return self.state.last_message_text

    @property
    def last_message_id(self) -> str | None:
        return self.state.last_message_id

    @property
    def last_activity_date(self) -> str | None:
        return self.state.last_activity_date

    @property
    def last_callback(self) -> dict[str, Any] | None:
        return self.state.last_callback

    @property
    def has_last_callback(self) -> bool:
        return self.last_callback is not None

    @property
    def last_callback_pin_verified(self) -> bool:
        callback = self.last_callback
        return bool(callback and callback.get("pin_verified"))

    @property
    def tasks(self) -> dict[str, TaskSpec]:
        return self.state.tasks

    @property
    def task_count(self) -> int:
        return len(self.tasks)

    @property
    def has_tasks(self) -> bool:
        return bool(self.tasks)

    @property
    def waves(self) -> list[list[str]]:
        return self.state.waves

    @property
    def wave_count(self) -> int:
        return len(self.waves)

    @property
    def current_wave_index(self) -> int:
        return self.state.current_wave_index

    @property
    def current_wave_task_ids(self) -> list[str]:
        if not self.waves or self.current_wave_index >= len(self.waves):
            return []
        return list(self.waves[self.current_wave_index])

    @property
    def pending_interrupt(self) -> PendingInterrupt | None:
        return self.state.pending_interrupt

    @property
    def has_pending_interrupt(self) -> bool:
        return self.pending_interrupt is not None

    @property
    def loaded_context(self) -> dict[str, Any]:
        return self.state.loaded_context

    @property
    def loaded_context_or_empty(self) -> dict[str, Any]:
        loaded_context = self.loaded_context
        return loaded_context if isinstance(loaded_context, dict) else {}

    @property
    def locale(self) -> str:
        return LocaleManager.normalize(self.loaded_context_or_empty.get("language")).value

    @property
    def outbox(self) -> list[dict[str, Any]]:
        return list(self.state.outbox)

    @property
    def context_frames(self) -> list[ContextFrame]:
        return list(self.state.context_frames)

    @property
    def referent_memory(self) -> ShortTermReferentMemory:
        return self.state.referent_memory

    @property
    def stashed_sessions(self) -> list[dict[str, Any]]:
        return list(self.state.stashed_sessions)

    @property
    def completed_tasks(self) -> list[TaskSpec]:
        return [task for task in self.tasks.values() if task.stage == TaskStage.COMPLETED]

    @property
    def failed_tasks(self) -> list[TaskSpec]:
        return [task for task in self.tasks.values() if task.stage == TaskStage.FAILED]

    @property
    def cancelled_tasks(self) -> list[TaskSpec]:
        return [task for task in self.tasks.values() if task.stage == TaskStage.CANCELLED]

    @property
    def all_tasks_terminal(self) -> bool:
        return self.has_tasks and all(task.stage in _TERMINAL_TASK_STAGES for task in self.tasks.values())

    @property
    def has_unblocked_nonterminal_wave(self) -> bool:
        if not self.has_tasks or self.has_pending_interrupt:
            return False
        if not self.current_wave_task_ids:
            return False
        return any(
            (task := self.tasks.get(task_id)) is not None and task.stage not in _TERMINAL_TASK_STAGES
            for task_id in self.current_wave_task_ids
        )

    @property
    def active_nonterminal_wave_task_shapes(self) -> list[dict[str, Any]]:
        return [
            {
                "task_id": task_id,
                "type": task.type,
                "stage": task.stage.value,
                "action": task.payload.get("action"),
            }
            for task_id in self.current_wave_task_ids
            if (task := self.tasks.get(task_id)) is not None and task.stage not in _TERMINAL_TASK_STAGES
        ]


def lifecycle_state_view(state: OrchestratorState) -> LifecycleStateView:
    return LifecycleStateView(state)


__all__ = ["LifecycleStateView", "lifecycle_state_view"]
