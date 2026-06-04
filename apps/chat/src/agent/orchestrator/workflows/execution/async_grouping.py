"""Async transaction grouping metadata for execution waves."""

from __future__ import annotations

from hashlib import sha1
from typing import Literal

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext
from apps.chat.src.agent.orchestrator.workflows.execution.task_mutations import update_task_payload

_TERMINAL_TRANSACTION_STAGES = {TaskStage.COMPLETED, TaskStage.FAILED, TaskStage.CANCELLED}


def _stamp_async_group_metadata(task: TaskSpec, ctx: ExecutionTurnContext) -> None:
    if task.type not in {"transfer", "airtime", "data"}:
        return

    transaction_types = {"transfer", "airtime", "data"}
    current_wave_task_ids = ctx.current_wave_task_ids or []
    current_wave_transaction_task_ids = [
        task_id
        for task_id in current_wave_task_ids
        if (wave_task := ctx.state.tasks.get(task_id)) is not None and wave_task.type in transaction_types
    ]

    previous_group_id = task.payload.get("async_group_id")
    candidate_task_ids = set(current_wave_transaction_task_ids)
    if previous_group_id:
        candidate_task_ids.update(
            task_id
            for task_id, sibling in ctx.state.tasks.items()
            if sibling.type in transaction_types
            and sibling.stage not in _TERMINAL_TRANSACTION_STAGES
            and sibling.payload.get("async_group_id") == previous_group_id
        )

    transaction_task_ids = [task_id for task_id in ctx.state.tasks if task_id in candidate_task_ids]
    for task_id in current_wave_transaction_task_ids:
        if task_id not in transaction_task_ids:
            transaction_task_ids.append(task_id)

    if not transaction_task_ids:
        return

    group_size = len(transaction_task_ids)
    group_kind: Literal["single", "multi_transfer", "mixed_batch"]
    if group_size == 1:
        group_kind = "single"
    elif all(ctx.state.tasks[task_id].type == "transfer" for task_id in transaction_task_ids):
        group_kind = "multi_transfer"
    else:
        group_kind = "mixed_batch"

    group_fingerprint = "|".join(
        [
            str(ctx.state.last_message_id or ""),
            str(ctx.state.current_wave_index),
            *transaction_task_ids,
        ]
    )
    group_id = sha1(group_fingerprint.encode("utf-8")).hexdigest()[:20]

    for index, task_id in enumerate(transaction_task_ids, start=1):
        grouped_task = ctx.state.tasks.get(task_id)
        if grouped_task is None or grouped_task.type not in transaction_types:
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
