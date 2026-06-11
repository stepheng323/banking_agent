"""Recipient review checkpoint before funding or authorization."""

from __future__ import annotations

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.common import TERMINAL_STAGES, _with_policy_notice
from apps.chat.src.agent.orchestrator.workflows.execution.locale import _state_locale
from apps.chat.src.agent.orchestrator.workflows.execution.prompts.prompting_queue import (
    _append_queued_notice,
    _queued_transaction_tasks_for_focus,
)
from apps.chat.src.agent.orchestrator.workflows.execution.task_access import existing_tasks
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
        (task_id, task)
        for task_id, task in existing_tasks(state, current_wave)
        if _is_live_transfer_task(task)
    ]
    if len(transfer_tasks) < 2:
        return []
    return [
        task_id
        for task_id, task in transfer_tasks
        if not _ready_for_batch_recipient_review(task)
    ]


def _recipient_line(payload: dict[str, Any]) -> str:
    alias = _text(payload.get("recipient_name")) or "Recipient"
    resolved = _text(payload.get("recipient_resolved_name")) or alias
    bank = _text(payload.get("recipient_bank_name") or payload.get("recipient_bank_code")) or "Bank"
    account = _text(payload.get("recipient_account") or payload.get("recipient_account_number"))
    heading = resolved if alias.casefold() == resolved.casefold() else f"{alias} → {resolved}"
    return f"• {heading}\n  {bank} • ****{_last4(account)}"


def _recipient_review_prompt(tasks: list[Any]) -> str:
    lines = ["Recipient review", "", "I found these recipients:", ""]
    for task in tasks:
        payload = task.payload if isinstance(task.payload, dict) else {}
        lines.append(_recipient_line(payload))
        lines.append("")
    lines.append("Are these correct? Reply yes to continue, or tell me what to change.")
    return "\n".join(lines).strip()


def maybe_request_recipient_review(
    *,
    state: OrchestratorState,
    current_wave: list[str],
    agg: ExecutionAccumulator,
) -> dict[str, Any] | None:
    """Block execution until resolved transfer recipients are explicitly approved."""
    recipient_input_fields = {"recipient_account", "recipient_bank_name"}
    if any(bool(set(fields) & recipient_input_fields) for _task_id, fields in agg.input_request_items()):
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

    task_ids = [task_id for task_id, _task in review_items]
    tasks = [task for _task_id, task in review_items]
    locale = _state_locale(state)
    for task_id, task in review_items:
        resume_fields = agg.input_fields_for(task_id)
        if resume_fields:
            task.payload["recipient_review_resume_fields"] = resume_fields
            resume_prompt = agg.prompt_for_task(task_id)
            if resume_prompt:
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

    prompt = _recipient_review_prompt(tasks)
    fields_by_task = {task_id: ["recipient_review_confirmed"] for task_id in task_ids}
    logger.info("recipient_review_checkpoint_blocked", task_ids=task_ids)
    agg.set_input_interrupt_outbox(
        task_ids=task_ids,
        fields_by_task=fields_by_task,
        prompt=prompt,
        entries=_with_policy_notice(state, [{"type": "say", "text": prompt}]),
    )
    agg.clear_policy_notice()
    return agg.to_updates()


__all__ = ["maybe_request_recipient_review", "recipient_review_signature"]
