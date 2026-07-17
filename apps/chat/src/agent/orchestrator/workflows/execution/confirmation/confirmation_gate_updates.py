"""Confirmation interrupt update builder for execution waves."""

from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.domain import TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.utils.actionable_payload import build_actionable_payload_for_tasks
from apps.chat.src.agent.orchestrator.workflows.conversation_closure import build_edit_update_notice
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.blocker_arbitration import gate_task_ids
from apps.chat.src.agent.orchestrator.workflows.execution.confirmation.confirmation_gate_summary import (
    _build_confirmation_gate_body_blocks,
    _build_confirmation_gate_summary,
)
from apps.chat.src.agent.orchestrator.workflows.execution.confirmation.confirmation_personality import (
    _confirmation_personality_context,
)
from apps.chat.src.agent.orchestrator.workflows.execution.confirmation.confirmation_update_message import (
    _compact_confirmation_update_message,
)
from apps.chat.src.agent.orchestrator.workflows.execution.loaded_context import loaded_context
from apps.chat.src.agent.orchestrator.workflows.execution.task_access import required_tasks
from apps.chat.src.agent.orchestrator.workflows.execution.wave.wave_state import (
    _fail_stalled_wave_tasks,
    next_wave_index,
)
from banking.presentation.formatters.transaction_confirmation_copy import build_confirmation_header
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _build_confirmation_gate_updates(
    *,
    state: OrchestratorState,
    current_wave: list[str],
    agg: ExecutionAccumulator,
    locale: str,
    task_ids: list[str] | None = None,
) -> dict[str, Any]:
    confirm_task_ids = task_ids or gate_task_ids(
        state=state,
        current_wave=current_wave,
        candidate_task_ids=agg.confirmation_task_ids(),
        stage=TaskStage.AWAITING_CONFIRMATION,
    )
    if not confirm_task_ids:
        stalled = _fail_stalled_wave_tasks(
            state=state,
            current_wave=current_wave,
            reason="confirmation gate produced no actionable tasks",
        )
        logger.error(
            "advance_wave_confirmation_gate_stalled",
            wave=current_wave,
            stalled_tasks=stalled,
            candidate_task_ids=agg.confirmation_task_ids(),
        )
        agg.set_current_wave_index(next_wave_index(state))
        return cast(dict[str, Any], agg.to_updates())

    accounts = loaded_context(state).account_rows
    summ = _build_confirmation_gate_summary(
        state=state,
        task_ids=confirm_task_ids,
        locale=locale,
        accounts=accounts,
    )
    body_blocks = _build_confirmation_gate_body_blocks(
        state=state,
        task_ids=confirm_task_ids,
        locale=locale,
        accounts=accounts,
    )

    confirmation_tasks = required_tasks(state, confirm_task_ids)
    first_task = confirmation_tasks[0][1]
    first_task_payload = first_task.payload.get("confirmation", {})
    snap = first_task_payload.get("snapshot", {})
    update_messages: list[str] = []
    for _task_id, task in confirmation_tasks:
        confirmation_payload = task.payload.get("confirmation", {})
        candidate = confirmation_payload.get("update_message")
        if isinstance(candidate, str) and candidate.strip():
            update_messages.append(candidate)
            continue
        previous_snapshot = confirmation_payload.get("previous_snapshot")
        current_snapshot = confirmation_payload.get("snapshot")
        if isinstance(previous_snapshot, dict) and isinstance(current_snapshot, dict):
            synthesized = build_edit_update_notice(
                previous_snapshot=previous_snapshot,
                current_snapshot=current_snapshot,
                locale=locale,
            )
            if synthesized:
                update_messages.append(synthesized)
    update_msg = _compact_confirmation_update_message(update_messages, locale)
    consumed_update_metadata = 0
    for _task_id, task in confirmation_tasks:
        confirmation_payload = task.payload.get("confirmation")
        if not isinstance(confirmation_payload, dict):
            continue
        for key in ("update_message", "previous_snapshot"):
            if key in confirmation_payload:
                confirmation_payload.pop(key, None)
                consumed_update_metadata += 1
    if consumed_update_metadata:
        logger.info(
            "confirmation_update_message_consumed",
            task_count=len(confirmation_tasks),
            metadata_field_count=consumed_update_metadata,
        )
    snapshots_by_task = {
        task_id: task.payload.get("confirmation", {}).get("snapshot", {}) for task_id, task in confirmation_tasks
    }
    authorized_idempotency_keys = [
        str(task.payload.get("idempotency_key") or "").strip()
        for _task_id, task in confirmation_tasks
        if str(task.payload.get("idempotency_key") or "").strip()
    ]
    authorization_idempotency_key = authorized_idempotency_keys[0] if authorized_idempotency_keys else None

    outbox: list[dict[str, Any]] = []
    if update_msg:
        outbox.append({"type": "say", "text": update_msg})

    confirmation_personality_context = _confirmation_personality_context(state, confirm_task_ids)

    outbox.append(
        {
            "type": "request_confirmation",
            "task_ids": confirm_task_ids,
            "header": build_confirmation_header(
                task_types=[task.type for _task_id, task in confirmation_tasks],
                locale=locale,
                task_count=len(confirm_task_ids),
                task_actions=[str(task.payload.get("action") or "") for _task_id, task in confirmation_tasks],
                personality_context=confirmation_personality_context,
            ),
            "summary": summ,
            "body_blocks": body_blocks,
            "snapshot": snap,
            "snapshots_by_task": snapshots_by_task,
            "idempotency_key": first_task.payload.get(
                "idempotency_key",
                "unknown",
            ),
            "actionable_payload": build_actionable_payload_for_tasks([task for _task_id, task in confirmation_tasks]),
        }
    )

    agg.set_confirmation_interrupt_outbox(
        task_ids=confirm_task_ids,
        prompt=summ,
        entries=outbox,
        authorization_idempotency_key=authorization_idempotency_key,
        authorized_task_idempotency_keys=authorized_idempotency_keys,
    )
    return cast(dict[str, Any], agg.to_updates())


__all__ = ["_build_confirmation_gate_updates"]
