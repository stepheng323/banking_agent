"""Typed graph-state patch produced by execution waves."""

from __future__ import annotations

from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.domain import ActiveSession, PendingInterrupt, TaskSpec


class ExecutionResultPatch:
    """Collects graph-state updates produced by execution."""

    def __init__(self, initial_updates: dict[str, Any] | None = None) -> None:
        self._updates: dict[str, Any] = dict(initial_updates or {})

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

    def set_tasks(self, tasks: dict[str, TaskSpec]) -> None:
        self._updates["tasks"] = tasks

    def get_tasks(self, default: dict[str, TaskSpec]) -> dict[str, TaskSpec]:
        return cast(dict[str, TaskSpec], self._updates.get("tasks", default))

    def set_waves(self, waves: list[list[str]]) -> None:
        self._updates["waves"] = waves

    def get_waves(self, default: list[list[str]]) -> list[list[str]]:
        return cast(list[list[str]], self._updates.get("waves", default))

    def set_session_stack(self, stack: list[ActiveSession]) -> None:
        self._updates["session_stack"] = stack

    def set_stashed_sessions(self, sessions: list[dict[str, Any]]) -> None:
        self._updates["stashed_sessions"] = sessions

    def clear_pending_query_clarification(self) -> None:
        self._updates["pending_query_clarification"] = None

    def set_pending_query_clarification(self, clarification: dict[str, Any] | None) -> None:
        self._updates["pending_query_clarification"] = clarification

    def set_last_interrupt(self, interrupt: Any) -> None:
        self._updates["last_interrupt"] = interrupt

    def clear_last_message_text(self) -> None:
        self._updates["last_message_text"] = None

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


__all__ = ["ExecutionResultPatch"]
