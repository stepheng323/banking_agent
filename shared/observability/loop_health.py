"""In-memory worker loop health state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class LoopHealth:
    """Health summary for a long-running reconciliation loop."""

    name: str
    enabled: bool = False
    running: bool = False
    last_started_at: str | None = None
    last_success_at: str | None = None
    last_failure_at: str | None = None
    last_error_type: str | None = None
    last_error_message: str | None = None
    success_count: int = 0
    failure_count: int = 0

    def mark_started(self) -> None:
        self.enabled = True
        self.running = True
        self.last_started_at = _now()

    def mark_disabled(self) -> None:
        self.enabled = False
        self.running = False

    def mark_success(self) -> None:
        self.running = True
        self.last_success_at = _now()
        self.success_count += 1

    def mark_failure(self, exc: Exception) -> None:
        self.running = True
        self.last_failure_at = _now()
        self.last_error_type = type(exc).__name__
        self.last_error_message = str(exc)[:240]
        self.failure_count += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "running": self.running,
            "last_started_at": self.last_started_at,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "last_error_type": self.last_error_type,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
        }


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class LoopHealthRegistry:
    """Registry of reconciliation loop health states."""

    loops: dict[str, LoopHealth] = field(default_factory=dict)

    def get(self, name: str) -> LoopHealth:
        if name not in self.loops:
            self.loops[name] = LoopHealth(name=name)
        return self.loops[name]

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {name: loop.as_dict() for name, loop in sorted(self.loops.items())}
