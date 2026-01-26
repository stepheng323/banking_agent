from typing import Any

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.execution.handlers import (
    ExecutionAggregation,
    ExecutionContext,
    handle_account_task,
    handle_airtime_task,
    handle_beneficiary_task,
    handle_data_task,
    handle_faq_task,
    handle_query_task,
    handle_support_task,
    handle_transfer_task,
)
from apps.core.src.agent.orchestrator.models.domain import (
    PendingInterrupt,
    TaskStage,
)
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def advance_wave(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    """Execution Node.

    Iterates through tasks in current wave.
    Invokes Domain Workers.
    Aggregates outcomes and sets PendingInterrupt if blocked.
    """
    if not state.waves or state.current_wave_index >= len(state.waves):
        logger.info("advance_wave_skip", index=state.current_wave_index, count=len(state.waves))
        return {}

    current_wave = state.waves[state.current_wave_index]
    logger.info("advance_wave", index=state.current_wave_index, tasks=current_wave)

    services = config["configurable"].get("services") or {}
    agg = ExecutionAggregation(state.tasks)

    ctx = ExecutionContext(
        state=state,
        config=config,
        services=services,
        current_wave_len=len(current_wave),
        agg=agg,
    )

    handlers = {
        "transfer": handle_transfer_task,
        "account": handle_account_task,
        "beneficiary": handle_beneficiary_task,
        "airtime": handle_airtime_task,
        "query": handle_query_task,
        "data": handle_data_task,
        "faq": handle_faq_task,
        "support": handle_support_task,
    }

    for task_id in current_wave:
        task = state.tasks.get(task_id)
        if not task:
            continue

        if task.stage in (TaskStage.COMPLETED, TaskStage.FAILED, TaskStage.CANCELLED):
            continue

        handler = handlers.get(task.type)
        if not handler:
            continue

        await handler(task, task_id, ctx)

    if agg.missing_fields_by_task:
        prompt_text = "\n".join(agg.prompts) or "I need some details."
        interrupt = PendingInterrupt(
            kind="input",
            task_ids=list(agg.missing_fields_by_task.keys()),
            fields_by_task=agg.missing_fields_by_task,
            prompt=prompt_text,
        )
        return {
            "pending_interrupt": interrupt,
            "tasks": state.tasks,
            "outbox": [{"type": "say", "text": prompt_text}],
        }

    updates = agg.updates

    if agg.needs_confirm_tasks:
        confirmation_payload = state.tasks[agg.needs_confirm_tasks[0]].payload["confirmation"]
        summ = confirmation_payload.get("summary", "Confirm transaction?")
        snap = confirmation_payload.get("snapshot", {})
        update_msg = confirmation_payload.get("update_message")

        interrupt = PendingInterrupt(
            kind="confirmation",
            task_ids=agg.needs_confirm_tasks,
        )

        outbox = []
        if update_msg:
            outbox.append({"type": "say", "text": update_msg})

        outbox.append(
            {
                "type": "request_confirmation",
                "task_ids": agg.needs_confirm_tasks,
                "summary": summ,
                "snapshot": snap,
                "idempotency_key": state.tasks[agg.needs_confirm_tasks[0]].payload.get(
                    "idempotency_key",
                    "unknown",
                ),
            }
        )

        updates["outbox"] = outbox
        updates["pending_interrupt"] = interrupt
        return updates

    if agg.needs_auth_tasks:
        first_task = state.tasks[agg.needs_auth_tasks[0]]
        summ = first_task.payload.get("confirmation", {}).get("summary", "Please enter your PIN.")
        snap = first_task.payload.get("confirmation", {}).get("snapshot", {})

        idem_key = first_task.payload.get("idempotency_key", "no-key")

        task_type = first_task.type
        reason = "Authorize Transaction"
        if task_type == "transfer":
            reason = "Transfer Authorization"
        elif task_type == "airtime":
            reason = "Airtime Purchase"
        elif task_type == "data":
            reason = "Data Purchase"

        interrupt = PendingInterrupt(kind="auth", task_ids=agg.needs_auth_tasks, auth_method="pin", prompt=summ)
        updates["outbox"] = [
            {
                "type": "auth_request",
                "method": "pin",
                "task_ids": agg.needs_auth_tasks,
                "idempotency_key": idem_key,
                "header": reason,
                "summary": summ,
            }
        ]
        updates["pending_interrupt"] = interrupt
        return updates

    all_terminal = all(
        state.tasks[task_id].stage in (TaskStage.COMPLETED, TaskStage.FAILED, TaskStage.CANCELLED)
        for task_id in current_wave
    )

    if all_terminal:
        updates["current_wave_index"] = state.current_wave_index + 1

    return updates
