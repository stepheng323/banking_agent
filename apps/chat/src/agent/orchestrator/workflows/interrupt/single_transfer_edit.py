"""Low-latency extraction path for amendments to one pending transfer."""

from __future__ import annotations

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.utils.task_payload_schedule import (
    derive_transfer_schedule_fields,
    infer_schedule_action_from_text,
)
from apps.chat.src.agent.orchestrator.workflows.execution.context_surface import context_surface
from apps.chat.src.agent.orchestrator.workflows.execution.loaded_context import loaded_context
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.input.input_continue import _continue_flow_updates
from apps.chat.src.agent.orchestrator.workflows.interrupt.reprompt.reprompt_flow import _reprompt_updates
from apps.chat.src.agent.orchestrator.workflows.interrupt.runtime import InterruptRuntime
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome
from shared.observability.llm import LLMCallDeadlineExceeded

_TRANSFER_EDIT_EVIDENCE = frozenset(
    {
        "amount",
        "transfer_all",
        "transfer_percentage",
        "recipient_name",
        "recipient_account",
        "recipient_bank_name",
        "source_bank_name",
        "source_account_index",
        "source_accounts",
        "use_dual_accounts",
        "explicit_split",
        "narration",
        "authored_narration",
        "user_note",
        "action",
        "schedule_mode",
        "recurrence_type",
        "schedule_timezone",
        "schedule_start_date",
        "schedule_time_local",
        "schedule_day_of_week",
        "schedule_day_of_month",
    }
)


def _single_pending_transfer_task(state: OrchestratorState, runtime: InterruptRuntime) -> tuple[str, Any] | None:
    if getattr(runtime.interrupt, "kind", None) != "confirmation":
        return None
    # A removed sibling means this is still a mutable batch, even if only one
    # task remains active.  Let the pending-action interpreter see the whole
    # active/removed context so requests such as "include the self transfer
    # again" restore the removed leg instead of being mistaken for a recipient
    # amendment to the remaining transfer.
    if runtime.state_view.removed_confirmation_tasks:
        return None
    task_ids = runtime.state_view.active_task_ids_for_interrupt(runtime.interrupt)
    if len(task_ids) != 1:
        return None
    task_id = task_ids[0]
    task = runtime.state_view.task(task_id)
    if task is None or task.type != "transfer" or task.stage != TaskStage.AWAITING_CONFIRMATION:
        return None
    return task_id, task


def _transfer_edit_context(state: OrchestratorState, runtime: InterruptRuntime) -> dict[str, Any]:
    context = loaded_context(state)
    surface = context_surface(state)
    return {
        "phone_number": state.phone_number,
        "channel": state.channel,
        "channel_identity": state.channel_identity,
        "user_id": context.user_id,
        "accounts": context.transaction_accounts_or_accounts,
        "all_accounts": context.accounts,
        "beneficiaries": context.beneficiaries,
        "referent_memory": surface.referent_memory_payload(),
        "language": runtime.state_view.current_locale,
        "required_fields": [],
        "previous_response": getattr(runtime.interrupt, "prompt", None),
        "confirmation_task_count": 1,
    }


async def resolve_single_transfer_confirmation_edit_updates(
    *,
    state: OrchestratorState,
    runtime: InterruptRuntime,
) -> dict[str, Any] | None:
    """Apply a supported one-transfer amendment without broad interrupt routing.

    The transfer worker supplies typed extraction evidence. A no-match stays on
    the normal pending-action/router path, so this shortcut cannot claim an
    arbitrary free-form message merely because a transfer is pending.
    """
    candidate = _single_pending_transfer_task(state, runtime)
    if candidate is None:
        return None
    task_id, task = candidate
    interpreter = getattr(runtime.services.transfer, "interpret_pending_confirmation_edit", None)
    if not callable(interpreter):
        return None

    schedule_action = infer_schedule_action_from_text(runtime.text)
    schedule_patch: dict[str, Any] = {}
    if schedule_action is not None:
        schedule_patch = {
            "action": schedule_action,
            **derive_transfer_schedule_fields(
                runtime.text,
                schedule_text=None,
                scheduled_text=None,
                recurring_flag=schedule_action == "recurring_transfer",
            ),
            "confirmation": {"confirmed": False},
        }

    logger.info("single_transfer_edit_fast_path", outcome="attempted")
    try:
        result = await interpreter(
            payload=dict(task.payload),
            context=_transfer_edit_context(state, runtime),
            user_message=runtime.text,
        )
    except LLMCallDeadlineExceeded as exc:
        logger.warning(
            "single_transfer_edit_deadline_exceeded",
            role=exc.role,
            deadline_seconds=exc.deadline_seconds,
        )
        updates = _reprompt_updates(state, runtime.interrupt)
        updates["outbox"] = [
            {
                "type": "say",
                "text": render_message("orchestrator.fallback.transfer_timeout", runtime.state_view.current_locale),
            }
        ]
        return updates
    patch = result.patch if getattr(result, "outcome", None) == TransactionOutcome.OK else {}
    if not isinstance(patch, dict):
        patch = {}
    combined_patch = {**patch, **schedule_patch}
    if not _TRANSFER_EDIT_EVIDENCE.intersection(combined_patch):
        logger.info("single_transfer_edit_fast_path", outcome="not_applicable")
        return None

    # The extraction result has already supplied the amendment. The later
    # transfer pipeline should validate, fund, and reconfirm it, not call the
    # extractor a second time for this same turn.
    payload_override = dict(combined_patch)
    payload_override["skip_extraction"] = True
    logger.info(
        "single_transfer_edit_fast_path",
        outcome="applied",
        patched_fields=sorted(_TRANSFER_EDIT_EVIDENCE.intersection(combined_patch)),
        pending_action_llm_avoided=True,
    )
    return _continue_flow_updates(
        state,
        runtime.interrupt,
        precomputed_payload_overrides={task_id: payload_override},
    )


__all__ = ["resolve_single_transfer_confirmation_edit_updates"]
