"""Typed delivery outcomes shared by direct delivery callers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

DeliveryAttemptStatus = Literal["delivered", "deduped_completed", "deduped_resumed", "failed"]


@dataclass(frozen=True, slots=True)
class DeliveryAttemptResult:
    """Structured direct-delivery outcome for internal callers."""

    status: DeliveryAttemptStatus
    message_ids: tuple[str, ...] = field(default_factory=tuple)
    error: str | None = None

    @property
    def delivered(self) -> bool:
        return self.status == "delivered"


__all__ = ["DeliveryAttemptResult", "DeliveryAttemptStatus"]
