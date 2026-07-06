"""Typed turn metadata access for execution orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState


@dataclass(frozen=True)
class ExecutionTurnMetadata:
    """Read-only typed facade over execution turn metadata."""

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
    def is_telegram(self) -> bool:
        return self.channel == "telegram"

    @property
    def last_message_text(self) -> str | None:
        return self.state.last_message_text

    def last_message_text_or(self, default: str) -> str:
        return self.last_message_text or default

    @property
    def last_message_id(self) -> str | None:
        return self.state.last_message_id

    @property
    def quoted_message_id(self) -> str | None:
        return self.state.quoted_message_id

    @property
    def pin_verified(self) -> bool:
        return self.state.pin_verified

    @property
    def authorization_context_payload(self) -> dict[str, Any] | None:
        if self.state.authorization_context is None:
            return None
        return self.state.authorization_context.model_dump()

    @property
    def pending_query_clarification(self) -> dict[str, Any] | None:
        return self.state.pending_query_clarification

    @property
    def has_pending_query_clarification(self) -> bool:
        return self.pending_query_clarification is not None

    @property
    def stashed_sessions(self) -> list[dict[str, Any]]:
        return self.state.stashed_sessions

    @property
    def has_stashed_sessions(self) -> bool:
        return bool(self.stashed_sessions)

    @property
    def latest_stashed_session(self) -> dict[str, Any] | None:
        if not self.stashed_sessions:
            return None
        return self.stashed_sessions[-1]

    @property
    def remaining_stashed_sessions(self) -> list[dict[str, Any]]:
        if not self.stashed_sessions:
            return []
        return self.stashed_sessions[:-1]


def turn_metadata(state: OrchestratorState) -> ExecutionTurnMetadata:
    return ExecutionTurnMetadata(state)


__all__ = ["ExecutionTurnMetadata", "turn_metadata"]
