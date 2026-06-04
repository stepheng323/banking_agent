"""Typed task mutation helpers for execution orchestration."""

from __future__ import annotations

from typing import Any, Literal

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage

TaskType = Literal[
    "transfer",
    "query",
    "airtime",
    "data",
    "account",
    "support",
    "faq",
    "beneficiary",
    "schedule",
    "orchestrator",
]


def set_task_stage(task: TaskSpec, stage: TaskStage) -> None:
    task.stage = stage


def set_task_type(task: TaskSpec, task_type: TaskType) -> None:
    task.type = task_type


def update_task_payload(task: TaskSpec, values: dict[str, Any]) -> None:
    task.payload.update(values)


def replace_task_payload(task: TaskSpec, values: dict[str, Any]) -> None:
    task.payload.clear()
    task.payload.update(values)


def set_task_payload_value(task: TaskSpec, key: str, value: Any) -> None:
    task.payload[key] = value


def remove_task_payload_values(task: TaskSpec, *keys: str) -> None:
    for key in keys:
        task.payload.pop(key, None)


def pop_task_payload_value(task: TaskSpec, key: str) -> Any:
    return task.payload.pop(key, None)


def complete_task(task: TaskSpec, *, receipt: Any = None) -> None:
    task.stage = TaskStage.COMPLETED
    if receipt:
        task.payload["receipt"] = receipt


def set_task_confirmation(
    task: TaskSpec,
    *,
    summary: Any,
    snapshot: Any,
    update_message: Any,
) -> None:
    confirmation = task.payload.setdefault("confirmation", {})
    confirmation["summary"] = summary
    confirmation["snapshot"] = snapshot
    if update_message:
        confirmation["update_message"] = update_message
    else:
        confirmation.pop("update_message", None)
    remove_task_payload_values(task, "transition_acknowledgment", "previous_confirmation_snapshot")


def fail_task(task: TaskSpec, error: str | None, extra_payload: dict[str, Any] | None = None) -> None:
    task.stage = TaskStage.FAILED
    if extra_payload:
        task.payload.update(extra_payload)
    task.payload["error"] = error


def cancel_task(task: TaskSpec, error: str) -> None:
    task.stage = TaskStage.CANCELLED
    task.payload["error"] = error


__all__ = [
    "cancel_task",
    "complete_task",
    "fail_task",
    "pop_task_payload_value",
    "remove_task_payload_values",
    "replace_task_payload",
    "set_task_confirmation",
    "set_task_payload_value",
    "set_task_stage",
    "set_task_type",
    "TaskType",
    "update_task_payload",
]
