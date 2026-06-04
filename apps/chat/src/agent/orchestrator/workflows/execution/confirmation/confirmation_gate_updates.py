"""Confirmation interrupt update builder for execution waves."""

from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.domain import PendingInterrupt, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.utils.actionable_payload import build_actionable_payload_for_tasks
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import (
    ExecutionAccumulator,
    ExecutionResultPatch,
)
from apps.chat.src.agent.orchestrator.workflows.execution.blocker_arbitration import gate_task_ids
from apps.chat.src.agent.orchestrator.workflows.execution.confirmation.confirmation_gate_summary import (
    _build_confirmation_gate_summary,
)
from apps.chat.src.agent.orchestrator.workflows.execution.confirmation.confirmation_personality import (
    _confirmation_personality_context,
)
from apps.chat.src.agent.orchestrator.workflows.execution.confirmation.confirmation_update_message import (
    _compact_confirmation_update_message,
)
from apps.chat.src.agent.orchestrator.workflows.execution.wave.wave_state import (
    _fail_stalled_wave_tasks,
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
    patch: ExecutionResultPatch,
    task_ids: list[str] | None = None,
) -> dict[str, Any]:
    confirm_task_ids = task_ids or gate_task_ids(
        state=state,
        current_wave=current_wave,
        candidate_task_ids=agg.needs_confirm_tasks,
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
            candidate_task_ids=agg.needs_confirm_tasks,
        )
        patch.set_update("current_wave_index", state.current_wave_index + 1)
        return cast(dict[str, Any], patch.to_updates())

    accounts_raw = state.loaded_context.get("accounts") or []
    accounts = [account for account in accounts_raw if isinstance(account, dict)]
    summ = _build_confirmation_gate_summary(
        state=state,
        task_ids=confirm_task_ids,
        locale=locale,
        accounts=accounts,
    )

    first_task_payload = state.tasks[confirm_task_ids[0]].payload.get("confirmation", {})
    snap = first_task_payload.get("snapshot", {})
    update_messages: list[str] = []
    for task_id in confirm_task_ids:
        confirmation_payload = state.tasks[task_id].payload.get("confirmation", {})
        candidate = confirmation_payload.get("update_message")
        if isinstance(candidate, str) and candidate.strip():
            update_messages.append(candidate)
    update_msg = _compact_confirmation_update_message(update_messages, locale)
    snapshots_by_task = {
        tid: state.tasks[tid].payload.get("confirmation", {}).get("snapshot", {}) for tid in confirm_task_ids
    }

    interrupt = PendingInterrupt(
        kind="confirmation",
        task_ids=confirm_task_ids,
        prompt=summ,
    )

    outbox: list[dict[str, Any]] = []
    if update_msg:
        outbox.append({"type": "say", "text": update_msg})

    confirmation_personality_context = _confirmation_personality_context(state, confirm_task_ids)

    outbox.append(
        {
            "type": "request_confirmation",
            "task_ids": confirm_task_ids,
            "header": build_confirmation_header(
                task_types=[state.tasks[task_id].type for task_id in confirm_task_ids if task_id in state.tasks],
                locale=locale,
                task_count=len(confirm_task_ids),
                task_actions=[
                    str(state.tasks[task_id].payload.get("action") or "")
                    for task_id in confirm_task_ids
                    if task_id in state.tasks
                ],
                personality_context=confirmation_personality_context,
            ),
            "summary": summ,
            "snapshot": snap,
            "snapshots_by_task": snapshots_by_task,
            "idempotency_key": state.tasks[confirm_task_ids[0]].payload.get(
                "idempotency_key",
                "unknown",
            ),
            "actionable_payload": build_actionable_payload_for_tasks(
                [state.tasks[task_id] for task_id in confirm_task_ids if task_id in state.tasks]
            ),
        }
    )

    patch.set_update("outbox", outbox)
    patch.set_update("pending_interrupt", interrupt)
    return cast(dict[str, Any], patch.to_updates())


__all__ = ["_build_confirmation_gate_updates"]
