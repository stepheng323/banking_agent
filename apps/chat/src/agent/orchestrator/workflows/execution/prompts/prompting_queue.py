"""Queued-task and acknowledgement helpers for execution prompts."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.common import TERMINAL_STAGES, TRANSACTION_TASK_TYPES
from apps.chat.src.agent.orchestrator.workflows.execution.task_access import existing_tasks
from banking.presentation.formatters.transaction_copy_context import format_amount_compact
from banking.presentation.formatters.transaction_intent_lines import format_intent_line
from banking.presentation.i18n.renderer import render_message


def _queued_transaction_tasks_for_focus(
    *,
    state: OrchestratorState,
    current_wave: list[str],
    focused_tid: str,
) -> list[Any]:
    queued: list[Any] = []
    for tid, task in existing_tasks(state, current_wave):
        if tid == focused_tid:
            continue
        if task.type not in TRANSACTION_TASK_TYPES:
            continue
        if task.stage in TERMINAL_STAGES:
            continue
        queued.append(task)
    return queued


def _append_queued_notice(
    *,
    prompt_text: str,
    queued_tasks: list[Any],
    locale: str,
) -> tuple[str, dict[str, Any] | None]:
    if not queued_tasks:
        return prompt_text, None

    queued_lines = [format_intent_line(task.type, task.payload, locale=locale) for task in queued_tasks]
    queued_lines = [line for line in queued_lines if line]
    if not queued_lines:
        return prompt_text, None

    queued_text = "\n".join(f"• {line}" for line in queued_lines)
    queue_notice = render_message(
        "orchestrator.execution.queued_next_notice",
        locale,
        {"queued_intents": queued_text},
    )
    merged_prompt = f"{prompt_text}\n\n{queue_notice}" if prompt_text else queue_notice
    queue_meta = {
        "queued_task_ids": [task.id for task in queued_tasks],
        "queued_task_types": [task.type for task in queued_tasks],
        "queued_intents": queued_lines,
    }
    return merged_prompt, queue_meta


def _ready_airtime_acknowledgements(
    *,
    state: OrchestratorState,
    current_wave: list[str],
    focused_tid: str,
    missing_fields_by_task: dict[str, list[str]],
) -> list[str]:
    acknowledgements: list[str] = []
    for tid, task in existing_tasks(state, current_wave):
        if tid == focused_tid:
            continue
        if task.type != "airtime" or task.stage in TERMINAL_STAGES:
            continue
        if tid in missing_fields_by_task:
            continue
        phone = str(task.payload.get("recipient_phone") or task.payload.get("phone") or "").strip()
        if not phone:
            continue
        amount = format_amount_compact(task.payload.get("amount"))
        acknowledgements.append(f"I'll also buy {amount} airtime for {phone}.")
    return acknowledgements


def _append_acknowledgements_to_prompt(prompt_text: str, acknowledgements: list[str]) -> str:
    if not acknowledgements:
        return prompt_text

    ack_text = " ".join(acknowledgements)
    prefix, separator, rest = prompt_text.partition("\n\n")
    if separator and prefix.startswith("I found "):
        return f"{prefix} {ack_text}\n\n{rest}"
    return f"{prompt_text}\n\n{ack_text}" if prompt_text else ack_text


__all__ = [
    "_append_acknowledgements_to_prompt",
    "_append_queued_notice",
    "_queued_transaction_tasks_for_focus",
    "_ready_airtime_acknowledgements",
]
