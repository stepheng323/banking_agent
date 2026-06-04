"""Auth interrupt update builder for execution waves."""

from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.domain import TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.utils.actionable_payload import build_actionable_payload_for_tasks
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.blocker_arbitration import gate_task_ids
from apps.chat.src.agent.orchestrator.workflows.execution.confirmation.confirmation_auth import (
    _auth_header_for_tasks,
)
from apps.chat.src.agent.orchestrator.workflows.execution.confirmation.confirmation_gate_summary import (
    _build_confirmation_gate_summary,
)
from apps.chat.src.agent.orchestrator.workflows.execution.loaded_context import loaded_context
from apps.chat.src.agent.orchestrator.workflows.execution.task_access import required_tasks
from apps.chat.src.agent.orchestrator.workflows.execution.wave.wave_state import (
    _fail_stalled_wave_tasks,
    next_wave_index,
)
from banking.presentation.i18n.renderer import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _build_auth_gate_updates(
    *,
    state: OrchestratorState,
    current_wave: list[str],
    agg: ExecutionAccumulator,
    locale: str,
    task_ids: list[str] | None = None,
) -> dict[str, Any]:
    auth_task_ids = task_ids or gate_task_ids(
        state=state,
        current_wave=current_wave,
        candidate_task_ids=agg.auth_task_ids(),
        stage=TaskStage.AWAITING_AUTH,
    )
    if not auth_task_ids:
        stalled = _fail_stalled_wave_tasks(
            state=state,
            current_wave=current_wave,
            reason="auth gate produced no actionable tasks",
        )
        logger.error(
            "advance_wave_auth_gate_stalled",
            wave=current_wave,
            stalled_tasks=stalled,
            candidate_task_ids=agg.auth_task_ids(),
        )
        agg.set_current_wave_index(next_wave_index(state))
        return cast(dict[str, Any], agg.to_updates())

    auth_tasks = required_tasks(state, auth_task_ids)
    first_task = auth_tasks[0][1]
    accounts = loaded_context(state).account_rows
    summ = _build_confirmation_gate_summary(
        state=state,
        task_ids=auth_task_ids,
        locale=locale,
        accounts=accounts,
    )
    if not summ:
        summ = render_message("orchestrator.execution.pin_prompt_default", locale)
    snap = first_task.payload.get("confirmation", {}).get("snapshot", {})
    snapshots_by_task = {
        task_id: task.payload.get("confirmation", {}).get("snapshot", {}) for task_id, task in auth_tasks
    }

    idem_key = first_task.payload.get("idempotency_key", "no-key")
    reason = _auth_header_for_tasks(state, auth_task_ids, locale=locale)

    agg.set_auth_interrupt_outbox(
        task_ids=auth_task_ids,
        prompt=summ,
        entries=[
            {
                "type": "auth_request",
                "method": "pin",
                "task_ids": auth_task_ids,
                "idempotency_key": idem_key,
                "header": reason,
                "summary": summ,
                "snapshot": snap,
                "snapshots_by_task": snapshots_by_task,
                "actionable_payload": build_actionable_payload_for_tasks([task for _task_id, task in auth_tasks]),
            }
        ],
    )
    return cast(dict[str, Any], agg.to_updates())


__all__ = ["_build_auth_gate_updates"]
