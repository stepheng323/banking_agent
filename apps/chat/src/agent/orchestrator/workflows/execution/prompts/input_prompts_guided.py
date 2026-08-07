"""Guided presentation for incomplete multi-task transaction batches."""

from __future__ import annotations

from typing import Any, Literal

from apps.chat.src.agent.orchestrator.models.domain import BatchInputContract, BatchInputSlot
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.common import _with_policy_notice
from apps.chat.src.agent.orchestrator.workflows.execution.last_interrupt import last_interrupt
from apps.chat.src.agent.orchestrator.workflows.execution.prompts.prompting_options import (
    _build_show_options_entry,
    clean_options_prompt,
)
from apps.chat.src.agent.orchestrator.workflows.execution.task_access import require_task
from banking.presentation.formatters.transaction_copy_context import format_amount_compact
from banking.presentation.i18n.message_keys import as_message_key
from banking.presentation.i18n.renderer import render_message
from shared.money import to_naira
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _slot_kind(task_type: str, field: str) -> Literal["selection", "amount", "account", "phone", "network", "other"]:
    if field in {"beneficiary_id", "referent_recipient_id", "data_plan_id"}:
        return "selection"
    if field == "amount":
        return "amount"
    if field in {"recipient_account", "recipient_bank_name", "source_account_id"}:
        return "account"
    if field in {"recipient_phone", "phone", "target_phone"}:
        return "phone"
    if field == "network":
        return "network"
    return "other"


def _batch_slots(
    *,
    current_wave: list[str],
    state: OrchestratorState,
    agg: ExecutionAccumulator,
) -> list[BatchInputSlot]:
    slots: list[BatchInputSlot] = []
    ordered_task_ids = list(enumerate(current_wave))
    source_indexes = [
        index
        for _, task_id in ordered_task_ids
        if (index := _source_clause_index(state=state, task_id=task_id)) is not None
    ]
    if source_indexes:
        ordered_task_ids.sort(
            key=lambda pair: (
                _source_clause_index(state=state, task_id=pair[1]) or max(source_indexes) + 1,
                pair[0],
            )
        )
    for _, task_id in ordered_task_ids:
        if not agg.has_input_request(task_id):
            continue
        task = require_task(state, task_id)
        for field in agg.input_fields_for(task_id):
            slots.append(BatchInputSlot(task_id=task_id, field=field, kind=_slot_kind(task.type, field)))
    return slots


def _has_options(*, task_id: str, agg: ExecutionAccumulator) -> bool:
    details = agg.details_for_task(task_id)
    return isinstance(details, dict) and isinstance(details.get("options"), list)


def _source_clause_index(*, state: OrchestratorState, task_id: str) -> int | None:
    raw_index = require_task(state, task_id).payload.get("source_clause_index")
    return raw_index if isinstance(raw_index, int) and raw_index > 0 else None


def _focus_slot(slots: list[BatchInputSlot], agg: ExecutionAccumulator) -> BatchInputSlot:
    for slot in slots:
        if slot.kind == "selection" and (_has_options(task_id=slot.task_id, agg=agg) or slot.field == "beneficiary_id"):
            return slot
    return slots[0]


def _slot_copy(*, slot: BatchInputSlot, state: OrchestratorState, locale: str) -> str:
    task = require_task(state, slot.task_id)
    payload = task.payload
    if slot.kind == "selection" and task.type == "transfer":
        if payload.get("is_self") is True:
            bank_name = str(payload.get("recipient_bank_name") or "").strip()
            amount = to_naira(payload.get("amount"))
            if bank_name and amount is not None and amount > 0:
                return render_message(
                    "orchestrator.execution.batch_input.self_transfer_pending",
                    locale,
                    {
                        "amount": format_amount_compact(amount),
                        "bank_name": bank_name,
                    },
                )
        amount = to_naira(payload.get("amount"))
        if amount is not None and amount > 0:
            return render_message(
                "orchestrator.execution.batch_input.choose_transfer_recipient",
                locale,
                {"amount": format_amount_compact(amount)},
            )
        return render_message("orchestrator.execution.batch_input.choose_transfer_recipient_no_amount", locale)
    if slot.kind == "amount" and task.type == "airtime":
        network = str(payload.get("network") or "").strip()
        key = (
            "orchestrator.execution.batch_input.airtime_amount_network"
            if network
            else "orchestrator.execution.batch_input.airtime_amount"
        )
        return render_message(as_message_key(key), locale, {"network": network})
    if slot.kind == "amount":
        return render_message("orchestrator.execution.batch_input.amount", locale)
    if slot.kind == "phone":
        return render_message("orchestrator.execution.batch_input.phone", locale)
    if slot.kind == "network":
        return render_message("orchestrator.execution.batch_input.network", locale)
    if slot.kind == "account":
        return render_message("orchestrator.execution.batch_input.account", locale)
    return render_message("orchestrator.execution.batch_input.detail", locale)


def selection_prompt_for_task(*, task: Any, locale: str) -> str | None:
    """Build a concise, task-aware lead for a recipient choice surface.

    The worker still owns candidate resolution and option payloads.  This
    helper only describes the typed slot being requested, so a sibling task
    can never leak its recipient label into the question.
    """
    if getattr(task, "type", None) != "transfer":
        return None
    payload = getattr(task, "payload", None)
    if not isinstance(payload, dict):
        return None
    recipient = str(payload.get("recipient_name") or "").strip()
    amount = to_naira(payload.get("amount"))
    if not recipient:
        # Candidate metadata is rendered separately as buttons (or a numbered
        # fallback).  Keep the lead useful even when a worker did not echo the
        # original recipient label into the task payload.
        if amount is not None and amount > 0:
            return render_message(
                "orchestrator.execution.batch_input.selection_prompt_generic",
                locale,
                {"amount": format_amount_compact(amount)},
            )
        return render_message(
            "orchestrator.execution.batch_input.selection_prompt_generic_no_amount",
            locale,
        )
    if amount is not None and amount > 0:
        return render_message(
            "orchestrator.execution.batch_input.selection_prompt",
            locale,
            {"recipient": recipient, "amount": format_amount_compact(amount)},
        )
    return render_message(
        "orchestrator.execution.batch_input.selection_prompt_no_amount",
        locale,
        {"recipient": recipient},
    )


def _resolved_focus_copy(*, state: OrchestratorState, previous: BatchInputContract, locale: str) -> str | None:
    slot = previous.focused_slot
    if slot.kind != "selection":
        return None
    try:
        task = require_task(state, slot.task_id)
    except Exception:
        return None
    if task.type != "transfer":
        return None
    payload = task.payload
    beneficiary_id = str(payload.get("beneficiary_id") or "").strip()
    if not beneficiary_id:
        return None
    amount = to_naira(payload.get("amount"))
    recipient = str(payload.get("recipient_name") or "").strip()
    resolved = str(payload.get("recipient_resolved_name") or "").strip()
    display = resolved if not recipient or recipient.casefold() == resolved.casefold() else f"{recipient} ({resolved})"
    if amount is None or amount <= 0 or not display:
        return None
    return render_message(
        "orchestrator.execution.batch_input.selection_resolved",
        locale,
        {"amount": format_amount_compact(amount), "recipient": display},
    )


def build_guided_batch_input_updates(
    *,
    state: OrchestratorState,
    current_wave: list[str],
    agg: ExecutionAccumulator,
    locale: str,
) -> dict[str, Any]:
    """Create one complete batch contract while displaying one clear next action."""
    slots = _batch_slots(current_wave=current_wave, state=state, agg=agg)
    if not slots:
        return agg.to_updates()
    focus = _focus_slot(slots, agg)
    previous = last_interrupt(state).interrupt
    previous_batch = previous.batch_input if previous and previous.kind == "input" else None
    # An interrupt may be retained as ``last_interrupt`` while a fresh
    # execution wave is being assembled.  Only acknowledge the previous
    # selection after that exact slot has disappeared from the new unresolved
    # slot set.  If it is still focused/unresolved, combining the old
    # acknowledgement with the current options produces contradictory copy:
    # "Done ..." followed by "Which one did you mean?".
    previous_focus_still_unresolved = bool(
        previous_batch is not None and previous_batch.focused_slot in slots
    )
    resolved_copy = (
        _resolved_focus_copy(state=state, previous=previous_batch, locale=locale)
        if previous_batch and not previous_focus_still_unresolved
        else None
    )
    remaining = [slot for slot in slots if slot != focus]
    focus_copy = _slot_copy(slot=focus, state=state, locale=locale)

    worker_prompt = agg.prompt_for_task(focus.task_id)

    parts: list[str] = []
    if resolved_copy:
        parts.append(resolved_copy)
        if worker_prompt:
            parts.append(
                render_message("orchestrator.execution.batch_input.next_prompt", locale, {"prompt": worker_prompt})
            )
        else:
            parts.append(render_message("orchestrator.execution.batch_input.next", locale, {"item": focus_copy}))
    else:
        selection_copy = (
            selection_prompt_for_task(task=require_task(state, focus.task_id), locale=locale)
            if focus.kind == "selection"
            else None
        )
        if selection_copy:
            parts.append(selection_copy)
        elif worker_prompt:
            parts.append(worker_prompt)
        else:
            parts.append(render_message("orchestrator.execution.batch_input.first", locale, {"item": focus_copy}))

    if remaining:
        # A logical input can occupy more than one typed field.  For example,
        # an unresolved recipient account has both an account-number and bank
        # slot, but the user should see one conversational request for
        # "account details", not the same item twice.
        remaining_labels = list(
            dict.fromkeys(
                _slot_copy(slot=slot, state=state, locale=locale) for slot in remaining
            )
        )
        remaining_copy = ", ".join(remaining_labels)
        parts.append(
            render_message(
                "orchestrator.execution.batch_input.still_needed",
                locale,
                {"items": remaining_copy},
            )
        )

    prompt = "\n\n".join(parts)

    details = agg.details_for_task(focus.task_id)
    options_entry = _build_show_options_entry(
        details=details,
        prompt_text=clean_options_prompt(prompt) if details else prompt,
        task_id=focus.task_id,
        focused_missing_fields=[focus.field],
    )
    entries: list[dict[str, Any]] = [options_entry] if options_entry else [{"type": "say", "text": prompt}]
    entries[0]["prompt_kind"] = "pending_input"
    if options_entry:
        options_entry["task_ids"] = list(dict.fromkeys(slot.task_id for slot in slots))
        options_entry["input_focus_task_id"] = focus.task_id
        options_entry["input_focus_field"] = focus.field

    completed_slot_count = 0
    if resolved_copy and previous_batch is not None:
        completed_slot_count = previous_batch.completed_slot_count + 1

    contract = BatchInputContract(
        slots=slots,
        focused_slot=focus,
        completed_slot_count=completed_slot_count,
    )
    logger.info(
        "guided_batch_input_created",
        unresolved_slot_count=len(slots),
        focused_kind=focus.kind,
        remaining_slot_count=len(remaining),
    )
    agg.set_input_interrupt_outbox(
        task_ids=list(dict.fromkeys(slot.task_id for slot in slots)),
        fields_by_task=agg.input_fields_by_task(),
        prompt=prompt,
        entries=_with_policy_notice(state, entries),
        metadata=agg.input_interrupt_metadata_for(focus.task_id),
        batch_input=contract,
    )
    agg.clear_policy_notice()
    return agg.to_updates()


__all__ = ["build_guided_batch_input_updates", "selection_prompt_for_task"]
