"""Resume-session helpers for orchestrator finalization."""

import time
from typing import Any

from apps.chat.src.agent.orchestrator.context.models import ContextFrame, ContextFrameType
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from banking.presentation.formatters.transaction_copy_context import format_amount_compact
from banking.presentation.i18n.renderer import render_message

TRANSACTION_TASK_TYPES = {"transfer", "airtime", "data"}
TERMINAL_TASK_STAGES = {TaskStage.COMPLETED.value, TaskStage.FAILED.value, TaskStage.CANCELLED.value}
STASH_RESUME_TTL_SECONDS = 1800


def _extract_interrupt_task_ids(pending_interrupt: Any) -> list[str]:
    if isinstance(pending_interrupt, dict):
        raw_task_ids = pending_interrupt.get("task_ids")
    else:
        raw_task_ids = getattr(pending_interrupt, "task_ids", None)
    if not isinstance(raw_task_ids, list):
        return []
    return [str(task_id) for task_id in raw_task_ids if isinstance(task_id, str)]


def _extract_task_stage_value(task: Any) -> str | None:
    stage: Any | None
    if isinstance(task, TaskSpec):
        stage = task.stage
    elif isinstance(task, dict):
        stage = task.get("stage")
    else:
        stage = getattr(task, "stage", None)

    if isinstance(stage, TaskStage):
        return stage.value
    if isinstance(stage, str):
        return stage
    return None


def is_resumable_stashed_session(stashed_session: dict[str, Any], *, now_ts: int) -> bool:
    stashed_at_ts = stashed_session.get("stashed_at_ts")
    if not isinstance(stashed_at_ts, int):
        return False
    if stashed_at_ts + STASH_RESUME_TTL_SECONDS <= now_ts:
        return False

    pending_interrupt = stashed_session.get("pending_interrupt")
    task_ids = _extract_interrupt_task_ids(pending_interrupt)
    if not task_ids:
        return False

    tasks = stashed_session.get("tasks")
    if not isinstance(tasks, dict):
        return False

    for task_id in task_ids:
        stage = _extract_task_stage_value(tasks.get(task_id))
        if stage and stage not in TERMINAL_TASK_STAGES:
            return True
    return False


def stashed_session_id(stashed_session: dict[str, Any]) -> str | None:
    stash_id = stashed_session.get("stash_id")
    return str(stash_id).strip() if stash_id else None


def _stashed_task_type(task: Any) -> str:
    if isinstance(task, TaskSpec):
        return task.type
    if isinstance(task, dict):
        return str(task.get("type") or "").strip()
    return str(getattr(task, "type", "") or "").strip()


def _stashed_task_payload(task: Any) -> dict[str, Any]:
    payload = task.payload if isinstance(task, TaskSpec) else task.get("payload") if isinstance(task, dict) else None
    return payload if isinstance(payload, dict) else {}


def _string(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = _string(value)
        if text:
            return text
    return ""


def _safe_resume_amount(value: Any) -> str:
    if value is None or value == "":
        return ""
    normalized_value = value.replace(",", "") if isinstance(value, str) else value
    try:
        amount = float(normalized_value)
    except (TypeError, ValueError):
        return ""
    if amount <= 0:
        return ""
    return format_amount_compact(amount)


def build_resume_prompt(session: dict[str, Any], *, locale: str) -> str:
    task_type = str(session.get("intent") or "").strip().lower()
    tasks = session.get("tasks")
    candidate_payload: dict[str, Any] = {}
    if isinstance(tasks, dict):
        for task in tasks.values():
            current_type = _stashed_task_type(task)
            if current_type in TRANSACTION_TASK_TYPES:
                task_type = current_type
                candidate_payload = _stashed_task_payload(task)
                break

    if task_type == "transfer":
        amount = _safe_resume_amount(candidate_payload.get("amount"))
        recipient = _first_non_empty(
            candidate_payload.get("recipient_resolved_name"),
            candidate_payload.get("recipient_name"),
        )
        if amount and recipient:
            return render_message(
                "orchestrator.finalize.resume_prompt_transfer_specific",
                locale,
                {"amount": amount, "recipient": recipient},
            )
        return render_message("orchestrator.finalize.resume_prompt_transfer_generic", locale)

    if task_type == "airtime":
        amount = _safe_resume_amount(candidate_payload.get("amount"))
        phone = _first_non_empty(
            candidate_payload.get("recipient_phone"),
            candidate_payload.get("phone_number"),
            candidate_payload.get("phone"),
        )
        if amount and phone:
            return render_message(
                "orchestrator.finalize.resume_prompt_airtime_specific",
                locale,
                {"amount": amount, "phone": phone},
            )
        return render_message("orchestrator.finalize.resume_prompt_airtime_generic", locale)

    if task_type == "data":
        amount = _safe_resume_amount(candidate_payload.get("amount"))
        phone = _first_non_empty(
            candidate_payload.get("target_phone"),
            candidate_payload.get("recipient_phone"),
            candidate_payload.get("phone_number"),
            candidate_payload.get("phone"),
        )
        if amount and phone:
            return render_message(
                "orchestrator.finalize.resume_prompt_data_specific",
                locale,
                {"amount": amount, "phone": phone},
            )
        return render_message("orchestrator.finalize.resume_prompt_data_generic", locale)

    intent = session.get("intent", render_message("orchestrator.session.default_intent", locale))
    return render_message("orchestrator.finalize.resume_prompt", locale, {"intent": intent})


def has_live_resume_prompt_frame(frames: list[ContextFrame]) -> bool:
    now = int(time.time())
    for frame in frames:
        if frame.frame_type != ContextFrameType.GENERIC:
            continue
        if frame.created_at_ts + frame.ttl_seconds <= now:
            continue
        if any(item.data.get("resume_prompt") is True for item in frame.items):
            return True
    return False
