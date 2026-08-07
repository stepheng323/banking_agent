"""Recipient review checkpoint before funding or authorization."""

from __future__ import annotations

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.common import TERMINAL_STAGES, _with_policy_notice
from apps.chat.src.agent.orchestrator.workflows.execution.last_interrupt import last_interrupt
from apps.chat.src.agent.orchestrator.workflows.execution.locale import _state_locale
from apps.chat.src.agent.orchestrator.workflows.execution.prompts.prompting_queue import (
    _append_queued_notice,
    _queued_transaction_tasks_for_focus,
)
from apps.chat.src.agent.orchestrator.workflows.execution.task_access import existing_tasks
from banking.presentation.formatters.transaction_intent_lines import format_intent_line
from banking.presentation.i18n.renderer import render_message
from shared.money import to_naira
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_REVIEWABLE_STAGES = {
    TaskStage.DRAFT,
    TaskStage.EXTRACTED,
    TaskStage.RESOLVED,
    TaskStage.VALIDATED,
    TaskStage.AWAITING_FUNDING_ADJUSTMENT,
    TaskStage.AWAITING_CONFIRMATION,
}

# These fields indicate that a recipient choice is still unresolved.  Keep
# this list broader than the worker's current schema: checkpoints created by
# older planner/materializer versions may use ``recipient_id`` while current
# transfer workers use ``beneficiary_id``.  Recipient review must never win
# over one of these choices.
_RECIPIENT_SELECTION_FIELDS = {
    "beneficiary_id",
    "referent_recipient_id",
    "recipient_id",
    "recipient_selection",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _last4(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[-4:] if len(digits) >= 4 else "????"


def recipient_review_signature(payload: dict[str, Any]) -> str | None:
    """Stable signature for the currently resolved recipient details."""
    account = _text(payload.get("recipient_account") or payload.get("recipient_account_number"))
    bank = _text(payload.get("recipient_bank_name") or payload.get("recipient_bank_code"))
    resolved = _text(payload.get("recipient_resolved_name") or payload.get("recipient_name"))
    resolution_mode = _text(payload.get("recipient_resolution_mode")) or "single_source"
    resolution_provider = _text(
        payload.get("recipient_resolution_provider") or payload.get("recipient_bank_code_provider")
    )
    if not (account and bank and resolved):
        return None
    return "|".join(
        (
            account.casefold(),
            bank.casefold(),
            resolved.casefold(),
            resolution_mode.casefold(),
            resolution_provider.casefold(),
        )
    )


def _needs_recipient_review(task: Any) -> bool:
    if task is None or task.type != "transfer" or task.stage in TERMINAL_STAGES:
        return False
    if task.stage not in _REVIEWABLE_STAGES:
        return False
    payload = task.payload if isinstance(task.payload, dict) else {}
    if not payload.get("recipient_review_required"):
        return False
    signature = recipient_review_signature(payload)
    if signature is None:
        return False
    if bool(payload.get("recipient_review_confirmed")) and payload.get("recipient_review_signature") == signature:
        return False
    confirmation = payload.get("confirmation")
    if isinstance(confirmation, dict) and confirmation.get("confirmed"):
        return False
    return True


def _task_payload(task: Any) -> dict[str, Any]:
    payload = getattr(task, "payload", None)
    return payload if isinstance(payload, dict) else {}


def _is_live_transfer_task(task: Any) -> bool:
    return bool(task and task.type == "transfer" and task.stage not in TERMINAL_STAGES)


def _has_recipient_destination(payload: dict[str, Any]) -> bool:
    return bool(
        _text(payload.get("recipient_account") or payload.get("recipient_account_number"))
        and _text(payload.get("recipient_bank_name") or payload.get("recipient_bank_code"))
    )


def _has_recipient_resolution(payload: dict[str, Any]) -> bool:
    return bool(
        payload.get("beneficiary_id")
        or payload.get("resolved_from_saved_beneficiary")
        or _text(payload.get("recipient_resolution_provider"))
        or _text(payload.get("recipient_resolved_name"))
    )


def _ready_for_batch_recipient_review(task: Any) -> bool:
    if not _is_live_transfer_task(task):
        return True
    payload = _task_payload(task)
    if not _has_recipient_destination(payload):
        return False
    if not _has_recipient_resolution(payload):
        return False
    return True


def _batch_recipient_review_waiting_task_ids(
    *,
    state: OrchestratorState,
    current_wave: list[str],
) -> list[str]:
    transfer_tasks = [
        (task_id, task) for task_id, task in existing_tasks(state, current_wave) if _is_live_transfer_task(task)
    ]
    if len(transfer_tasks) < 2:
        return []
    return [task_id for task_id, task in transfer_tasks if not _ready_for_batch_recipient_review(task)]


def _has_unresolved_recipient_selection(
    *,
    state: OrchestratorState,
    current_wave: list[str],
    agg: ExecutionAccumulator,
) -> bool:
    """Return whether any live leg still needs a recipient choice.

    The accumulator is authoritative after workers have run, but a review
    checkpoint can also be reached before the current wave dispatches.  In
    that case candidate payloads are the only signal that a selection is
    pending.  Checking both sources closes the race that used to render a
    self-transfer review beside the recipient option card.
    """
    for _task_id, fields in agg.input_request_items():
        if _RECIPIENT_SELECTION_FIELDS.intersection(fields):
            return True

    for task_id, task in existing_tasks(state, current_wave):
        if not _is_live_transfer_task(task):
            continue
        payload = _task_payload(task)
        if payload.get("beneficiary_id") or payload.get("referent_recipient_id"):
            continue
        for key in ("beneficiary_candidates", "referent_recipient_candidates"):
            candidates = payload.get(key)
            if isinstance(candidates, list) and candidates:
                logger.debug(
                    "recipient_review_deferred_for_pending_selection",
                    task_id=task_id,
                    candidate_kind=key,
                    candidate_count=len(candidates),
                )
                return True
    return False


def _recipient_line(payload: dict[str, Any], *, locale: str = "en") -> str:
    bank = _text(payload.get("recipient_bank_name") or payload.get("recipient_bank_code")) or "Bank"
    if payload.get("is_self") is True:
        heading = render_message("transfer.resolve.my_bank_name", locale, {"bank_name": bank})
    else:
        alias = _text(payload.get("recipient_name")) or "Recipient"
        resolved = _text(payload.get("recipient_resolved_name")) or alias
        heading = resolved if alias.casefold() == resolved.casefold() else f"{alias} → {resolved}"
    account = _text(payload.get("recipient_account") or payload.get("recipient_account_number"))
    return f"• {heading}\n  {bank} • ****{_last4(account)}"


def _ready_batch_item_line(payload: dict[str, Any], *, locale: str) -> str:
    """Render a ready sibling with its amount and masked destination."""
    intent = format_intent_line("transfer", payload, locale=locale)
    if not intent:
        return ""
    bank = _text(payload.get("recipient_bank_name") or payload.get("recipient_bank_code")) or "Bank"
    account = _text(payload.get("recipient_account") or payload.get("recipient_account_number"))
    return f"• {intent}\n  {bank} • ****{_last4(account)}"


def _recipient_review_prompt(
    tasks: list[Any],
    *,
    locale: str = "en",
    ready_sibling_tasks: list[Any] | None = None,
) -> str:
    lines = [
        render_message("orchestrator.execution.batch_input.recipient_review_heading", locale),
        "",
        render_message("orchestrator.execution.batch_input.recipient_review_intro", locale),
        "",
    ]
    for task in tasks:
        payload = task.payload if isinstance(task.payload, dict) else {}
        lines.append(_recipient_line(payload, locale=locale))
        lines.append("")
    if ready_sibling_tasks:
        sibling_lines: list[str] = []
        for task in ready_sibling_tasks:
            payload = task.payload if isinstance(task.payload, dict) else {}
            line = _ready_batch_item_line(payload, locale=locale)
            if line:
                sibling_lines.append(line)
        if sibling_lines:
            lines.extend(
                [
                    render_message(
                        "orchestrator.execution.batch_input.recipient_review_other_items",
                        locale,
                        {"items": "\n".join(sibling_lines)},
                    ),
                    "",
                ]
            )
    lines.append(
        render_message(
            "orchestrator.execution.batch_input.recipient_review_confirm",
            locale,
        )
    )
    return "\n".join(lines).strip()


def maybe_request_recipient_review(
    *,
    state: OrchestratorState,
    current_wave: list[str],
    agg: ExecutionAccumulator,
) -> dict[str, Any] | None:
    """Block execution until resolved transfer recipients are explicitly approved."""
    # Recipient review is downstream of recipient resolution.  In a mixed
    # batch, a ready sibling must never replace an unresolved beneficiary
    # selection (or destination details) with a review/confirmation surface;
    # source-account selection may still follow an already-resolved recipient
    # review, preserving the established funding flow.
    if _has_unresolved_recipient_selection(state=state, current_wave=current_wave, agg=agg):
        logger.debug(
            "recipient_review_deferred_until_batch_inputs_resolved",
            unresolved_task_count=agg.input_request_count(),
        )
        return None
    # Recipient review is a batch-wide checkpoint.  Any remaining input
    # (source account, amount, phone, or another domain slot) must be handled
    # first; otherwise the user sees a destination review followed by a
    # second, contradictory prompt for the same batch.
    if agg.input_request_count() > 0:
        logger.debug(
            "recipient_review_deferred_for_unresolved_batch_input",
            unresolved_task_count=agg.input_request_count(),
        )
        return None

    review_items = [
        (task_id, task) for task_id, task in existing_tasks(state, current_wave) if _needs_recipient_review(task)
    ]
    if not review_items:
        return None

    waiting_task_ids = _batch_recipient_review_waiting_task_ids(state=state, current_wave=current_wave)
    if waiting_task_ids:
        logger.debug(
            "recipient_review_waiting_for_batch_recipient_readiness",
            review_task_count=len(review_items),
            waiting_task_count=len(waiting_task_ids),
        )
        return None

    review_task_ids = [task_id for task_id, _task in review_items]
    tasks = [task for _task_id, task in review_items]
    locale = _state_locale(state)
    review_task_id_set = set(review_task_ids)
    previous = last_interrupt(state).interrupt
    guided_batch_scope = bool(
        previous
        and isinstance(previous.metadata, dict)
        and isinstance(previous.metadata.get("batch_task_ids"), list)
    )
    ready_sibling_tasks: list[Any] = []
    for sibling_id, sibling in existing_tasks(state, current_wave):
        if sibling_id in review_task_id_set or not _is_live_transfer_task(sibling):
            continue
        if _needs_recipient_review(sibling) or not _ready_for_batch_recipient_review(sibling):
            continue
        payload = _task_payload(sibling)
        amount = payload.get("amount")
        if amount is None:
            confirmation = payload.get("confirmation")
            snapshot = confirmation.get("snapshot") if isinstance(confirmation, dict) else None
            amount = snapshot.get("amount") if isinstance(snapshot, dict) else None
        normalized_amount = to_naira(amount)
        if (
            normalized_amount is not None
            and normalized_amount > 0
            and _text(payload.get("recipient_bank_name") or payload.get("recipient_bank_code"))
        ):
            ready_sibling_tasks.append(sibling)
    # A ready sibling is part of the same recipient checkpoint, even when it
    # did not itself require disambiguation.  Keeping it in the interrupt
    # scope is what lets approval resume and rebuild one batch review instead
    # of accepting only the focused external leg.
    task_ids = [
        *review_task_ids,
        *[
            task.id
            for task in ready_sibling_tasks
            if getattr(task, "id", None)
            and task.id not in review_task_ids
            and guided_batch_scope
        ],
    ]
    guided_batch_input = bool(previous and previous.batch_input)
    for task_id, task in review_items:
        resume_fields = agg.input_fields_for(task_id)
        if resume_fields:
            task.payload["recipient_review_resume_fields"] = resume_fields
            resume_prompt = agg.prompt_for_task(task_id)
            if resume_prompt:
                queue_meta: dict[str, Any] | None = None
                if not guided_batch_input:
                    queued_tasks = _queued_transaction_tasks_for_focus(
                        state=state,
                        current_wave=current_wave,
                        focused_tid=task_id,
                    )
                    resume_prompt, queue_meta = _append_queued_notice(
                        prompt_text=resume_prompt,
                        queued_tasks=queued_tasks,
                        locale=locale,
                    )
                task.payload["recipient_review_resume_prompt"] = resume_prompt
                resume_entry: dict[str, Any] = {
                    "type": "say",
                    "text": resume_prompt,
                    "prompt_kind": "pending_input",
                }
                if queue_meta is not None:
                    resume_entry["queue"] = queue_meta
                task.payload["recipient_review_resume_outbox"] = resume_entry
        task.stage = TaskStage.AWAITING_CONFIRMATION

    prompt = _recipient_review_prompt(
        tasks,
        locale=locale,
        ready_sibling_tasks=ready_sibling_tasks,
    )
    fields_by_task = {task_id: ["recipient_review_confirmed"] for task_id in task_ids}
    logger.info(
        "recipient_review_checkpoint_blocked",
        task_ids=task_ids,
        ready_sibling_count=len(ready_sibling_tasks),
    )
    agg.set_input_interrupt_outbox(
        task_ids=task_ids,
        fields_by_task=fields_by_task,
        prompt=prompt,
        entries=_with_policy_notice(state, [{"type": "say", "text": prompt}]),
    )
    agg.clear_policy_notice()
    return agg.to_updates()


__all__ = ["maybe_request_recipient_review", "recipient_review_signature"]
