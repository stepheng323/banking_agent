"""Async transaction grouping metadata for execution waves."""

from __future__ import annotations

from hashlib import sha1
from typing import Literal

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext
from apps.chat.src.agent.orchestrator.workflows.execution.task_access import existing_tasks, iter_tasks
from apps.chat.src.agent.orchestrator.workflows.execution.task_mutations import update_task_payload
from apps.chat.src.agent.orchestrator.workflows.execution.turn_metadata import turn_metadata
from apps.chat.src.agent.orchestrator.workflows.execution.wave.wave_state import current_wave_index

_TERMINAL_TRANSACTION_STAGES = {TaskStage.COMPLETED, TaskStage.FAILED, TaskStage.CANCELLED}


def _stamp_async_group_metadata(task: TaskSpec, ctx: ExecutionTurnContext) -> None:
    if task.type not in {"transfer", "airtime", "data"}:
        return

    transaction_types = {"transfer", "airtime", "data"}
    current_wave_task_ids = ctx.current_wave_task_ids or []
    current_wave_transaction_task_ids = [
        task_id
        for task_id, wave_task in existing_tasks(ctx.state, current_wave_task_ids)
        if wave_task.type in transaction_types
    ]

    previous_group_id = task.payload.get("async_group_id")
    candidate_task_ids = set(current_wave_transaction_task_ids)
    if previous_group_id:
        candidate_task_ids.update(
            task_id
            for task_id, sibling in iter_tasks(ctx.state)
            if sibling.type in transaction_types
            and sibling.stage not in _TERMINAL_TRANSACTION_STAGES
            and sibling.payload.get("async_group_id") == previous_group_id
        )

    transaction_task_ids = [task_id for task_id, _task in iter_tasks(ctx.state) if task_id in candidate_task_ids]
    for task_id in current_wave_transaction_task_ids:
        if task_id not in transaction_task_ids:
            transaction_task_ids.append(task_id)

    if not transaction_task_ids:
        return

    group_size = len(transaction_task_ids)
    transaction_tasks = existing_tasks(ctx.state, transaction_task_ids)
    group_kind: Literal["single", "multi_transfer", "mixed_batch"]
    if group_size == 1:
        group_kind = "single"
    elif all(grouped_task.type == "transfer" for _task_id, grouped_task in transaction_tasks):
        group_kind = "multi_transfer"
    else:
        group_kind = "mixed_batch"

    group_fingerprint = "|".join(
        [
            str(turn_metadata(ctx.state).last_message_id or ""),
            str(current_wave_index(ctx.state)),
            *transaction_task_ids,
        ]
    )
    group_id = sha1(group_fingerprint.encode("utf-8")).hexdigest()[:20]

    for index, (_task_id, grouped_task) in enumerate(transaction_tasks, start=1):
        if grouped_task.type not in transaction_types:
            continue
        update_task_payload(
            grouped_task,
            {
                "async_group_id": group_id,
                "async_group_size": group_size,
                "async_group_kind": group_kind,
                "async_group_index": index,
            },
        )


__all__ = ["_stamp_async_group_metadata"]
