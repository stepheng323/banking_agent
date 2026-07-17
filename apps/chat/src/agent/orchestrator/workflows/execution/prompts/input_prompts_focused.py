"""Focused input-prompt updates for one task in an execution wave."""

from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.common import (
    TERMINAL_STAGES,
    _with_policy_notice,
)
from apps.chat.src.agent.orchestrator.workflows.execution.last_interrupt import last_interrupt
from apps.chat.src.agent.orchestrator.workflows.execution.prompts.prompting_options import (
    _build_show_options_entry,
    _compact_prompt_for_options,
)
from apps.chat.src.agent.orchestrator.workflows.execution.prompts.prompting_queue import (
    _append_acknowledgements_to_prompt,
    _append_queued_notice,
    _queued_transaction_tasks_for_focus,
    _ready_airtime_acknowledgements,
)
from apps.chat.src.agent.orchestrator.workflows.execution.prompts.prompting_recipients import (
    _recipient_prompt_label,
)
from apps.chat.src.agent.orchestrator.workflows.execution.task_access import (
    existing_tasks,
    get_task,
    require_task,
)
from apps.chat.src.agent.orchestrator.workflows.execution.task_mutations import set_task_payload_value
from banking.presentation.formatters.transaction_slot_prompts import format_transaction_slot_prompt
from banking.presentation.formatters.transfer_input_prompts import format_single_transfer_recipient_prompt


def _build_focused_missing_field_updates(
    *,
    state: OrchestratorState,
    current_wave: list[str],
    agg: ExecutionAccumulator,
    locale: str,
    focused_tid: str,
) -> dict[str, Any]:
    focused_task = require_task(state, focused_tid)
    focused_name = (
        focused_task.payload.get("recipient_name")
        or focused_task.payload.get("recipient_resolved_name")
        or "this recipient"
    )
    interrupt = last_interrupt(state)
    just_resolved_tid = None
    just_resolved_name = None
    just_resolved_bank = None
    if interrupt.task_ids:
        for tid in interrupt.task_ids:
            task = get_task(state, tid)
            if not agg.has_input_request(tid) and task:
                just_resolved_tid = tid
                just_resolved_name = _recipient_prompt_label(cast(dict[str, Any], task.payload))
                just_resolved_bank = task.payload.get("recipient_bank_name")
                break

    found_names = []
    if just_resolved_tid is None:
        for tid, task in existing_tasks(state, current_wave):
            if task.stage in TERMINAL_STAGES:
                continue
            if task.type != "transfer":
                continue
            if not agg.has_input_request(tid):
                if name := _recipient_prompt_label(cast(dict[str, Any], task.payload)):
                    if name not in found_names:
                        found_names.append(name)

    focused_missing_fields = agg.input_fields_for(focused_tid)
    focused_worker_prompt = agg.prompt_for_task(focused_tid)
    focused_details = agg.details_for_task(focused_tid)
    has_structured_options = isinstance(focused_details, dict) and isinstance(focused_details.get("options"), list)
    transfer_recipient_fields = {"recipient_account", "recipient_bank_name"}
    is_transfer_recipient_prompt = bool(set(focused_missing_fields) & transfer_recipient_fields)
    if focused_worker_prompt and ("beneficiary_id" in focused_missing_fields or has_structured_options):
        prompt_text = _compact_prompt_for_options(focused_worker_prompt)
    elif focused_worker_prompt and not is_transfer_recipient_prompt:
        prompt_text = focused_worker_prompt
    else:
        prompt_text = format_single_transfer_recipient_prompt(
            focused_name=focused_name,
            focused_missing_fields=focused_missing_fields,
            just_resolved_name=just_resolved_name,
            just_resolved_bank=just_resolved_bank,
            found_names=found_names,
            locale=locale,
        )
    if len(current_wave) == 1 and not ("beneficiary_id" in focused_missing_fields or has_structured_options):
        prompt_text = format_transaction_slot_prompt(
            task_type=focused_task.type,
            payload=focused_task.payload,
            missing_fields=focused_missing_fields,
            fallback_prompt=prompt_text,
            locale=locale,
        )
    prompt_text = _append_acknowledgements_to_prompt(
        prompt_text,
        _ready_airtime_acknowledgements(
            state=state,
            current_wave=current_wave,
            focused_tid=focused_tid,
            missing_fields_by_task=agg.input_fields_by_task(),
        ),
    )
    queued_tasks = _queued_transaction_tasks_for_focus(
        state=state,
        current_wave=current_wave,
        focused_tid=focused_tid,
    )
    prompt_text, queue_meta = _append_queued_notice(
        prompt_text=prompt_text,
        queued_tasks=queued_tasks,
        locale=locale,
    )

    for tid, task in existing_tasks(state, current_wave):
        if tid == focused_tid:
            continue
        name = task.payload.get("recipient_resolved_name") or task.payload.get("recipient_name")
        if name and (not agg.has_input_request(tid) or tid == just_resolved_tid):
            set_task_payload_value(task, "recipient_ui_confirmed", True)
    options_entry = _build_show_options_entry(
        details=focused_details,
        prompt_text=prompt_text,
        task_id=focused_tid,
        focused_missing_fields=focused_missing_fields,
    )
    outbox_entries = [options_entry] if options_entry else [{"type": "say", "text": prompt_text}]
    outbox_entries[0]["prompt_kind"] = "pending_input"
    if queue_meta is not None:
        outbox_entries[0]["queue"] = queue_meta
    agg.set_input_interrupt_outbox(
        task_ids=[focused_tid],
        fields_by_task={focused_tid: agg.input_fields_for(focused_tid)},
        prompt=prompt_text,
        entries=_with_policy_notice(state, outbox_entries),
        metadata=agg.input_interrupt_metadata_for(focused_tid),
    )
    agg.clear_policy_notice()
    return agg.to_updates()


__all__ = ["_build_focused_missing_field_updates"]
