"""Mutable reducer surface for one execution wave."""

from __future__ import annotations

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import ActiveSession, PendingInterrupt, TaskSpec
from apps.chat.src.agent.orchestrator.workflows.execution.result_patch import ExecutionResultPatch


class ExecutionAccumulator:
    """Collects state and output mutations while a wave executes."""

    def __init__(self, tasks: dict[str, TaskSpec]) -> None:
        self.result_patch = ExecutionResultPatch({"tasks": tasks})
        self.missing_fields_by_task: dict[str, list[str]] = {}
        self.details_by_task: dict[str, dict[str, Any]] = {}
        self.needs_confirm_tasks: list[str] = []
        self.needs_auth_tasks: list[str] = []
        self.prompts: list[str] = []
        self.prompts_by_task: dict[str, str] = {}
        self.feedback_messages: list[str] = []
        self.source_bank_hints: list[str] = []

    def set_context_frames(self, context_frames: Any) -> None:
        self.result_patch.set_context_frames(context_frames)

    def set_referent_memory(self, referent_memory: Any) -> None:
        self.result_patch.set_referent_memory(referent_memory)

    def set_tasks(self, tasks: dict[str, Any]) -> None:
        self.result_patch.set_tasks(tasks)

    def get_tasks(self, default: dict[str, Any]) -> dict[str, Any]:
        return self.result_patch.get_tasks(default)

    def set_waves(self, waves: list[list[str]]) -> None:
        self.result_patch.set_waves(waves)

    def get_waves(self, default: list[list[str]]) -> list[list[str]]:
        return self.result_patch.get_waves(default)

    def set_current_wave_index(self, index: int) -> None:
        self.result_patch.set_current_wave_index(index)

    def has_current_wave_index(self) -> bool:
        return self.result_patch.has_current_wave_index()

    def set_pending_interrupt(self, interrupt: PendingInterrupt | None) -> None:
        self.result_patch.set_pending_interrupt(interrupt)

    def has_pending_interrupt(self) -> bool:
        return self.result_patch.has_pending_interrupt()

    def clear_pending_interrupt(self) -> None:
        self.result_patch.set_pending_interrupt(None)

    def set_session_stack(self, stack: list[ActiveSession]) -> None:
        self.result_patch.set_session_stack(stack)

    def set_stashed_sessions(self, sessions: list[dict[str, Any]]) -> None:
        self.result_patch.set_stashed_sessions(sessions)

    def clear_stashed_query_session(self) -> None:
        self.result_patch.clear_stashed_query_session()

    def set_last_interrupt(self, interrupt: Any) -> None:
        self.result_patch.set_last_interrupt(interrupt)

    def clear_last_message_text(self) -> None:
        self.result_patch.clear_last_message_text()

    def to_updates(self) -> dict[str, Any]:
        return self.result_patch.to_updates()

    def set_outbox(self, entries: list[dict[str, Any]]) -> None:
        self.result_patch.set_outbox(entries)

    def set_interrupt_outbox(self, interrupt: PendingInterrupt, entries: list[dict[str, Any]]) -> None:
        self.set_pending_interrupt(interrupt)
        self.set_outbox(entries)

    def get_outbox(self) -> list[dict[str, Any]]:
        return self.result_patch.get_outbox()

    def clear_policy_notice(self) -> None:
        self.result_patch.clear_policy_notice()

    def add_outbox(self, entry: dict[str, Any]) -> None:
        self.result_patch.append_outbox(entry)

    def extend_outbox(self, entries: list[dict[str, Any]] | None) -> None:
        if entries:
            self.result_patch.extend_outbox(entries)

    def say(self, text: str | None) -> None:
        if text:
            self.add_outbox({"type": "say", "text": text})

    def add_prompt(self, prompt: str | None, task_id: str | None = None) -> None:
        if prompt:
            self.prompts.append(prompt)
            if task_id:
                self.prompts_by_task[task_id] = prompt

    def add_feedback_message(self, message: str | None) -> None:
        if message:
            self.feedback_messages.append(message)

    def add_source_bank_hint(self, hint: Any) -> None:
        if hint:
            self.source_bank_hints.append(str(hint))

    def add_confirmation_task(self, task_id: str) -> None:
        self.needs_confirm_tasks.append(task_id)

    def add_auth_task(self, task_id: str) -> None:
        self.needs_auth_tasks.append(task_id)

    def remove_missing_fields(self, task_id: str) -> None:
        self.missing_fields_by_task.pop(task_id, None)

    def remove_missing_input_request(self, task_id: str) -> None:
        self.remove_missing_fields(task_id)
        self.prompts_by_task.pop(task_id, None)
        self.details_by_task.pop(task_id, None)

    def replace_missing_fields(self, task_id: str, fields: list[str]) -> None:
        self.missing_fields_by_task[task_id] = fields

    def add_missing_fields(self, task_id: str, fields: list[str] | None) -> None:
        if fields:
            self.missing_fields_by_task[task_id] = fields

    def add_details(self, task_id: str, details: dict[str, Any] | None) -> None:
        if details:
            self.details_by_task[task_id] = details


__all__ = ["ExecutionAccumulator"]
