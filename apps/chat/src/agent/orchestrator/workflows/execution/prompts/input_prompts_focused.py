"""Focused input-prompt updates for one task in an execution wave."""

from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.common import (
    TERMINAL_STAGES,
    _with_policy_notice,
)
from apps.chat.src.agent.orchestrator.workflows.execution.last_interrupt import last_interrupt
from apps.chat.src.agent.orchestrator.workflows.execution.prompts.input_prompts_guided import (
    selection_prompt_for_task,
)
from apps.chat.src.agent.orchestrator.workflows.execution.prompts.prompting_options import (
    _build_show_options_entry,
    _compact_prompt_for_options,
    clean_options_prompt,
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


def _dedupe_prompt_sections(prompt: str) -> str:
    """Remove repeated paragraphs introduced by worker/queue acknowledgements."""
    sections = [section.strip() for section in prompt.split("\n\n") if section.strip()]
    deduped: list[str] = []
    for section in sections:
        if not deduped or section != deduped[-1]:
            deduped.append(section)
    return "\n\n".join(deduped)


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
    transfer_recipient_fields = {"recipient_account", "recipient_bank_name"}
    is_transfer_recipient_prompt = bool(set(focused_missing_fields) & transfer_recipient_fields)
    is_recipient_selection = bool(
        {"beneficiary_id", "referent_recipient_id"}.intersection(focused_missing_fields)
    )
    # Preserve worker-owned wording for a single-task interrupt.  The typed,
    # batch-aware lead is only needed when a choice gates sibling work; this
    # keeps existing ambiguity and channel-specific copy intact while making
    # mixed batches explicit.
    use_typed_batch_selection_copy = is_recipient_selection and len(current_wave) > 1
    if use_typed_batch_selection_copy:
        prompt_text = selection_prompt_for_task(task=focused_task, locale=locale) or _compact_prompt_for_options(
            focused_worker_prompt or ""
        )
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
    is_amount_suggestion = (
        isinstance(focused_details, dict)
        and focused_details.get("option_context") == "TRANSFER_AMOUNT_SUGGESTION"
    )
    if len(current_wave) == 1 and not is_recipient_selection and not is_amount_suggestion:
        prompt_text = format_transaction_slot_prompt(
            task_type=focused_task.type,
            payload=focused_task.payload,
            missing_fields=focused_missing_fields,
            fallback_prompt=prompt_text,
            locale=locale,
        )
    if not is_recipient_selection:
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
    if use_typed_batch_selection_copy:
        # A recipient choice is a batch-wide gate.  Keep this card focused on
        # the one decision the user can make now.  Ready sibling summaries
        # used to be appended here, which produced a rowdy pseudo-review
        # beside the option buttons and made an incomplete batch look ready.
        # The final combined review is the first place where all legs are
        # shown together.
        queued_tasks = []
    prompt_text, queue_meta = _append_queued_notice(
        prompt_text=prompt_text,
        queued_tasks=queued_tasks,
        locale=locale,
    )
    prompt_text = _dedupe_prompt_sections(prompt_text)

    for tid, task in existing_tasks(state, current_wave):
        if tid == focused_tid:
            continue
        name = task.payload.get("recipient_resolved_name") or task.payload.get("recipient_name")
        if name and (not agg.has_input_request(tid) or tid == just_resolved_tid):
            set_task_payload_value(task, "recipient_ui_confirmed", True)
    options_entry = _build_show_options_entry(
        details=focused_details,
        prompt_text=clean_options_prompt(prompt_text) if focused_details else prompt_text,
        task_id=focused_tid,
        focused_missing_fields=focused_missing_fields,
    )
    outbox_entries = [options_entry] if options_entry else [{"type": "say", "text": prompt_text}]
    outbox_entries[0]["prompt_kind"] = "pending_input"
    if queue_meta is not None:
        outbox_entries[0]["queue"] = queue_meta
    # A focused choice can still gate a multi-operation wave.  Keep the
    # visible interrupt focused on the slot the user must answer, but retain
    # the complete live batch scope in metadata.  On the next turn the input
    # reducer uses this scope to re-run already prepared siblings (for
    # example, a self-transfer) and rebuild one confirmation for the whole
    # batch.  Without this, the sibling remains in ``AWAITING_CONFIRMATION``
    # while only the selected recipient leg is resumed, producing a partial
    # review.
    interrupt_metadata = agg.input_interrupt_metadata_for(focused_tid)
    if len(current_wave) > 1 and use_typed_batch_selection_copy:
        batch_task_ids = [
            task_id
            for task_id, task in existing_tasks(state, current_wave)
            if task.stage not in TERMINAL_STAGES and task.type in {"transfer", "airtime", "data"}
        ]
        if len(batch_task_ids) > 1:
            interrupt_metadata = {
                **interrupt_metadata,
                "batch_task_ids": batch_task_ids,
            }
    agg.set_input_interrupt_outbox(
        task_ids=[focused_tid],
        fields_by_task={focused_tid: agg.input_fields_for(focused_tid)},
        prompt=prompt_text,
        entries=_with_policy_notice(state, outbox_entries),
        metadata=interrupt_metadata,
    )
    agg.clear_policy_notice()
    return agg.to_updates()


__all__ = ["_build_focused_missing_field_updates"]
