"""Typed task read helpers for execution orchestration."""

from __future__ import annotations

from typing import Any, TypedDict

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.common import TERMINAL_STAGES


class TaskLogShape(TypedDict):
    task_id: str
    type: str
    stage: str
    action: Any


def task_map(state: OrchestratorState) -> dict[str, TaskSpec]:
    return state.tasks


def get_task(state: OrchestratorState, task_id: str) -> TaskSpec | None:
    return state.tasks.get(task_id)


def require_task(state: OrchestratorState, task_id: str) -> TaskSpec:
    return state.tasks[task_id]


def iter_tasks(state: OrchestratorState) -> list[tuple[str, TaskSpec]]:
    return list(state.tasks.items())


def existing_tasks(state: OrchestratorState, task_ids: list[str]) -> list[tuple[str, TaskSpec]]:
    return [(task_id, task) for task_id in task_ids if (task := state.tasks.get(task_id)) is not None]


def required_tasks(state: OrchestratorState, task_ids: list[str]) -> list[tuple[str, TaskSpec]]:
    return [(task_id, require_task(state, task_id)) for task_id in task_ids]


def non_terminal_tasks(state: OrchestratorState, task_ids: list[str]) -> list[tuple[str, TaskSpec]]:
    return [(task_id, task) for task_id, task in existing_tasks(state, task_ids) if task.stage not in TERMINAL_STAGES]


def non_terminal_task_ids(state: OrchestratorState, task_ids: list[str]) -> list[str]:
    return [task_id for task_id, _task in non_terminal_tasks(state, task_ids)]


def all_existing_tasks_terminal(state: OrchestratorState, task_ids: list[str]) -> bool:
    return all(task.stage in TERMINAL_STAGES for _task_id, task in existing_tasks(state, task_ids))


def task_types_for_ids(state: OrchestratorState, task_ids: list[str]) -> set[str]:
    return {task.type for _task_id, task in existing_tasks(state, task_ids)}


def task_log_shapes(state: OrchestratorState, task_ids: list[str]) -> list[TaskLogShape]:
    return [
        {
            "task_id": task_id,
            "type": task.type,
            "stage": task.stage.value,
            "action": task.payload.get("action"),
        }
        for task_id, task in existing_tasks(state, task_ids)
    ]


__all__ = [
    "TaskLogShape",
    "all_existing_tasks_terminal",
    "existing_tasks",
    "get_task",
    "iter_tasks",
    "non_terminal_task_ids",
    "non_terminal_tasks",
    "require_task",
    "required_tasks",
    "task_log_shapes",
    "task_map",
    "task_types_for_ids",
]
