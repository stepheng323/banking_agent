"""Mutable reducer surface for one execution wave."""

from __future__ import annotations

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import PendingInterrupt, TaskSpec


class ExecutionResultPatch:
    """Collects graph-state updates produced by execution."""

    def __init__(self, initial_updates: dict[str, Any] | None = None) -> None:
        self._updates: dict[str, Any] = dict(initial_updates or {})

    def set_update(self, key: str, value: Any) -> None:
        self._updates[key] = value

    def get_update(self, key: str, default: Any = None) -> Any:
        return self._updates.get(key, default)

    def has_update(self, key: str) -> bool:
        return key in self._updates

    def set_current_wave_index(self, index: int) -> None:
        self._updates["current_wave_index"] = index

    def has_current_wave_index(self) -> bool:
        return "current_wave_index" in self._updates

    def set_pending_interrupt(self, interrupt: PendingInterrupt | None) -> None:
        self._updates["pending_interrupt"] = interrupt

    def has_pending_interrupt(self) -> bool:
        return "pending_interrupt" in self._updates

    def set_outbox(self, entries: list[dict[str, Any]]) -> None:
        self._updates["outbox"] = entries

    def get_outbox(self) -> list[dict[str, Any]]:
        outbox = self._updates.get("outbox", [])
        return outbox if isinstance(outbox, list) else []

    def clear_policy_notice(self) -> None:
        self._updates["policy_notice"] = None

    def set_context_frames(self, context_frames: Any) -> None:
        self._updates["context_frames"] = context_frames

    def set_referent_memory(self, referent_memory: Any) -> None:
        self._updates["referent_memory"] = referent_memory

    def append_outbox(self, entry: dict[str, Any]) -> None:
        outbox = self._updates.setdefault("outbox", [])
        if isinstance(outbox, list):
            outbox.append(entry)

    def extend_outbox(self, entries: list[dict[str, Any]]) -> None:
        outbox = self._updates.setdefault("outbox", [])
        if isinstance(outbox, list):
            outbox.extend(entries)

    def to_updates(self) -> dict[str, Any]:
        return self._updates


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

    def set_update(self, key: str, value: Any) -> None:
        self.result_patch.set_update(key, value)

    def get_update(self, key: str, default: Any = None) -> Any:
        return self.result_patch.get_update(key, default)

    def has_update(self, key: str) -> bool:
        return self.result_patch.has_update(key)

    def apply_context_updates_to(self, patch: ExecutionResultPatch) -> None:
        if self.has_update("context_frames"):
            patch.set_context_frames(self.get_update("context_frames"))
        if self.has_update("referent_memory"):
            patch.set_referent_memory(self.get_update("referent_memory"))

    def to_updates(self) -> dict[str, Any]:
        return self.result_patch.to_updates()

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

    def add_missing_fields(self, task_id: str, fields: list[str] | None) -> None:
        if fields:
            self.missing_fields_by_task[task_id] = fields

    def add_details(self, task_id: str, details: dict[str, Any] | None) -> None:
        if details:
            self.details_by_task[task_id] = details


__all__ = ["ExecutionAccumulator", "ExecutionResultPatch"]
