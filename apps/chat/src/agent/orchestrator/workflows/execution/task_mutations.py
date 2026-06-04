"""Typed task mutation helpers for execution orchestration."""

from __future__ import annotations

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage


def fail_task(task: TaskSpec, error: str, extra_payload: dict[str, Any] | None = None) -> None:
    task.stage = TaskStage.FAILED
    if extra_payload:
        task.payload.update(extra_payload)
    task.payload["error"] = error


def cancel_task(task: TaskSpec, error: str) -> None:
    task.stage = TaskStage.CANCELLED
    task.payload["error"] = error


__all__ = [
    "cancel_task",
    "fail_task",
]
