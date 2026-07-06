"""Mutable reducer surface for one execution wave."""

from __future__ import annotations

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import ActiveSession, PendingInterrupt, TaskSpec
from apps.chat.src.agent.orchestrator.workflows.execution.result_patch import ExecutionResultPatch


class ExecutionAccumulator:
    """Collects state and output mutations while a wave executes."""

    def __init__(self, tasks: dict[str, TaskSpec], *, initial_outbox: list[dict[str, Any]] | None = None) -> None:
        initial_updates: dict[str, Any] = {"tasks": tasks}
        if initial_outbox:
            initial_updates["outbox"] = list(initial_outbox)
        self._result_patch = ExecutionResultPatch(initial_updates)
        self._missing_fields_by_task: dict[str, list[str]] = {}
        self._details_by_task: dict[str, dict[str, Any]] = {}
        self._needs_confirm_tasks: list[str] = []
        self._needs_auth_tasks: list[str] = []
        self._prompts: list[str] = []
        self._prompts_by_task: dict[str, str] = {}
        self._feedback_messages: list[str] = []
        self._source_bank_hints: list[str] = []

    def set_context_frames(self, context_frames: Any) -> None:
        self._result_patch.set_context_frames(context_frames)

    def set_referent_memory(self, referent_memory: Any) -> None:
        self._result_patch.set_referent_memory(referent_memory)

    def set_tasks(self, tasks: dict[str, TaskSpec]) -> None:
        self._result_patch.set_tasks(tasks)

    def get_tasks(self, default: dict[str, TaskSpec]) -> dict[str, TaskSpec]:
        return self._result_patch.get_tasks(default)

    def set_waves(self, waves: list[list[str]]) -> None:
        self._result_patch.set_waves(waves)

    def get_waves(self, default: list[list[str]]) -> list[list[str]]:
        return self._result_patch.get_waves(default)

    def set_current_wave_index(self, index: int) -> None:
        self._result_patch.set_current_wave_index(index)

    def has_current_wave_index(self) -> bool:
        return self._result_patch.has_current_wave_index()

    def _set_pending_interrupt(self, interrupt: PendingInterrupt | None) -> None:
        self._result_patch.set_pending_interrupt(interrupt)

    def has_pending_interrupt(self) -> bool:
        return self._result_patch.has_pending_interrupt()

    def clear_pending_interrupt(self) -> None:
        self._result_patch.set_pending_interrupt(None)

    def set_session_stack(self, stack: list[ActiveSession]) -> None:
        self._result_patch.set_session_stack(stack)

    def set_stashed_sessions(self, sessions: list[dict[str, Any]]) -> None:
        self._result_patch.set_stashed_sessions(sessions)

    def clear_pending_query_clarification(self) -> None:
        self._result_patch.clear_pending_query_clarification()

    def set_pending_query_clarification(self, clarification: dict[str, Any] | None) -> None:
        self._result_patch.set_pending_query_clarification(clarification)

    def set_last_interrupt(self, interrupt: Any) -> None:
        self._result_patch.set_last_interrupt(interrupt)

    def clear_last_message_text(self) -> None:
        self._result_patch.clear_last_message_text()

    def to_updates(self) -> dict[str, Any]:
        return self._result_patch.to_updates()

    def set_outbox(self, entries: list[dict[str, Any]]) -> None:
        self._result_patch.set_outbox(entries)

    def _set_interrupt_outbox(self, interrupt: PendingInterrupt, entries: list[dict[str, Any]]) -> None:
        self._set_pending_interrupt(interrupt)
        self.set_outbox(entries)

    def set_input_interrupt_outbox(
        self,
        *,
        task_ids: list[str],
        fields_by_task: dict[str, list[str]],
        prompt: str,
        entries: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._set_interrupt_outbox(
            PendingInterrupt(
                kind="input",
                task_ids=task_ids,
                fields_by_task=fields_by_task,
                prompt=prompt,
                metadata=metadata or {},
            ),
            entries,
        )

    def set_confirmation_interrupt_outbox(
        self,
        *,
        task_ids: list[str],
        prompt: str,
        entries: list[dict[str, Any]],
        authorization_idempotency_key: str | None = None,
        authorized_task_idempotency_keys: list[str] | None = None,
    ) -> None:
        self._set_interrupt_outbox(
            PendingInterrupt(
                kind="confirmation",
                task_ids=task_ids,
                prompt=prompt,
                authorization_idempotency_key=authorization_idempotency_key,
                authorized_task_idempotency_keys=authorized_task_idempotency_keys or [],
            ),
            entries,
        )

    def set_auth_interrupt_outbox(
        self,
        *,
        task_ids: list[str],
        prompt: str,
        entries: list[dict[str, Any]],
        authorization_idempotency_key: str | None = None,
        authorized_task_idempotency_keys: list[str] | None = None,
    ) -> None:
        self._set_interrupt_outbox(
            PendingInterrupt(
                kind="auth",
                task_ids=task_ids,
                auth_method="pin",
                prompt=prompt,
                authorization_idempotency_key=authorization_idempotency_key,
                authorized_task_idempotency_keys=authorized_task_idempotency_keys or [],
            ),
            entries,
        )

    def get_outbox(self) -> list[dict[str, Any]]:
        return self._result_patch.get_outbox()

    def clear_policy_notice(self) -> None:
        self._result_patch.clear_policy_notice()

    def add_outbox(self, entry: dict[str, Any]) -> None:
        self._result_patch.append_outbox(entry)

    def extend_outbox(self, entries: list[dict[str, Any]] | None) -> None:
        if entries:
            self._result_patch.extend_outbox(entries)

    def say(self, text: str | None) -> None:
        if text:
            self.add_outbox({"type": "say", "text": text})

    def input_task_ids(self) -> list[str]:
        return list(self._missing_fields_by_task)

    def input_request_count(self) -> int:
        return len(self._missing_fields_by_task)

    def has_input_request(self, task_id: str) -> bool:
        return task_id in self._missing_fields_by_task

    def input_fields_for(self, task_id: str) -> list[str]:
        return list(self._missing_fields_by_task.get(task_id, []))

    def input_fields_by_task(self) -> dict[str, list[str]]:
        return {task_id: list(fields) for task_id, fields in self._missing_fields_by_task.items()}

    def input_request_items(self) -> list[tuple[str, list[str]]]:
        return [(task_id, list(fields)) for task_id, fields in self._missing_fields_by_task.items()]

    def single_input_task_id(self) -> str | None:
        if len(self._missing_fields_by_task) != 1:
            return None
        return next(iter(self._missing_fields_by_task))

    def prompt_for_task(self, task_id: str) -> str | None:
        return self._prompts_by_task.get(task_id)

    def details_for_task(self, task_id: str) -> dict[str, Any] | None:
        details = self._details_by_task.get(task_id)
        return dict(details) if details else None

    def prompt_history(self) -> list[str]:
        return list(self._prompts)

    def feedback_messages_for_prompt(self) -> list[str]:
        return list(self._feedback_messages)

    def first_source_bank_hint(self) -> str | None:
        return self._source_bank_hints[0] if self._source_bank_hints else None

    def confirmation_task_ids(self) -> list[str]:
        return list(self._needs_confirm_tasks)

    def auth_task_ids(self) -> list[str]:
        return list(self._needs_auth_tasks)

    def add_prompt(self, prompt: str | None, task_id: str | None = None) -> None:
        if prompt:
            self._prompts.append(prompt)
            if task_id:
                self._prompts_by_task[task_id] = prompt

    def add_feedback_message(self, message: str | None) -> None:
        if message:
            self._feedback_messages.append(message)

    def add_source_bank_hint(self, hint: Any) -> None:
        if hint:
            self._source_bank_hints.append(str(hint))

    def add_confirmation_task(self, task_id: str) -> None:
        self._needs_confirm_tasks.append(task_id)

    def add_auth_task(self, task_id: str) -> None:
        self._needs_auth_tasks.append(task_id)

    def remove_missing_fields(self, task_id: str) -> None:
        self._missing_fields_by_task.pop(task_id, None)

    def remove_missing_input_request(self, task_id: str) -> None:
        self.remove_missing_fields(task_id)
        self._prompts_by_task.pop(task_id, None)
        self._details_by_task.pop(task_id, None)

    def replace_missing_fields(self, task_id: str, fields: list[str]) -> None:
        self._missing_fields_by_task[task_id] = fields

    def add_missing_fields(self, task_id: str, fields: list[str] | None) -> None:
        if fields:
            self._missing_fields_by_task[task_id] = fields

    def add_details(self, task_id: str, details: dict[str, Any] | None) -> None:
        if details:
            self._details_by_task[task_id] = details


__all__ = ["ExecutionAccumulator"]
