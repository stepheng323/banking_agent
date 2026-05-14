"""Quoted replay planner helper functions."""

import json
from typing import Any, cast

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.nodes.planner.context import (
    build_quoted_replay_context_from_summary,
    get_or_build_turn_context_summary,
)
from shared.i18n import render_message
from shared.types.quoted_replay import QuotedReplayInterpretation
from shared.utils.logging import get_logger

logger = get_logger(__name__)

QUOTED_REPLAY_MIN_CONFIDENCE = 0.75
QUOTED_REPLAY_PAYLOAD_PREVIEW_MAX_CHARS = 1200
QUOTED_REPLAY_TASK_PREVIEW_LIMIT = 5
QUOTED_REPLAY_PAYLOAD_KEYS = (
    "task_type",
    "action",
    "amount",
    "beneficiary_id",
    "recipient_name",
    "recipient_resolved_name",
    "recipient_phone",
    "target_phone",
    "recipient_account",
    "recipient_account_number",
    "recipient_bank_code",
    "recipient_bank_name",
    "resolved_from_saved_beneficiary",
    "source_bank_name",
    "source_account_id",
    "source_account_index",
    "source_account_number",
    "source_affinity_mode",
    "narration",
    "network",
    "plan_code",
    "plan_name",
    "final_status",
    "error_message",
    "failure_category",
)

_REPLAY_TASK_TYPES = {"transfer", "airtime", "data"}


def _compact_quoted_task_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key in QUOTED_REPLAY_PAYLOAD_KEYS if (value := payload.get(key)) not in (None, "")}


def _compact_quoted_payload_for_prompt(payload: dict[str, Any]) -> str:
    if not payload:
        return "{}"

    if isinstance(payload.get("tasks"), list):
        raw_tasks = [task for task in payload["tasks"] if isinstance(task, dict)]
        compact_payload: dict[str, Any] = {
            "task_type": payload.get("task_type"),
            "task_ids": payload.get("task_ids"),
            "task_types": payload.get("task_types"),
            "tasks": [_compact_quoted_task_payload(task) for task in raw_tasks[:QUOTED_REPLAY_TASK_PREVIEW_LIMIT]],
        }
        overflow = len(raw_tasks) - QUOTED_REPLAY_TASK_PREVIEW_LIMIT
        if overflow > 0:
            compact_payload["more_tasks"] = overflow
    else:
        compact_payload = _compact_quoted_task_payload(payload)

    serialized = json.dumps(compact_payload, ensure_ascii=True)
    if len(serialized) <= QUOTED_REPLAY_PAYLOAD_PREVIEW_MAX_CHARS:
        return serialized
    return serialized[: QUOTED_REPLAY_PAYLOAD_PREVIEW_MAX_CHARS - 3] + "..."


def _build_quoted_replay_context(state: OrchestratorState) -> str:
    summary, _ = get_or_build_turn_context_summary(state, path_label="planner_path")
    return build_quoted_replay_context_from_summary(
        summary,
        quoted_message_id=state.quoted_message_id,
        has_quote=state.has_quote,
    )


def _build_quoted_replay_context_with_payload(state: OrchestratorState, quoted_payload: dict[str, Any]) -> str:
    payload_preview = _compact_quoted_payload_for_prompt(quoted_payload)
    summary, _ = get_or_build_turn_context_summary(state, path_label="planner_path")
    return build_quoted_replay_context_from_summary(
        summary,
        quoted_message_id=state.quoted_message_id,
        has_quote=state.has_quote,
        quoted_payload_preview=payload_preview,
    )


def _next_quoted_replay_task_id(state: OrchestratorState, task_type: str, existing_ids: set[str] | None = None) -> str:
    seen = set(existing_ids or set())
    seen.update(state.tasks.keys())
    idx = 1
    task_id = f"quoted_replay_{task_type}_{idx}"
    while task_id in seen:
        idx += 1
        task_id = f"quoted_replay_{task_type}_{idx}"
    return task_id


async def _load_quoted_actionable_payload(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any] | None:
    repo = config["configurable"].get("actionable_message_repo")
    if repo is None or not state.quoted_message_id:
        return None

    user_id = (state.loaded_context or {}).get("user_id") or state.user_id
    if not user_id:
        logger.info("quoted_replay_actionable_lookup_skipped", reason="missing_user_id")
        return None

    try:
        row = await repo.get_by_channel_message_id_for_user(state.quoted_message_id, str(user_id))
    except Exception as exc:
        logger.warning("quoted_replay_actionable_lookup_failed", error=str(exc))
        return None

    if not row:
        return None

    message_data = row.get("message_data") if isinstance(row, dict) else getattr(row, "message_data", None)
    if isinstance(message_data, dict):
        return dict(message_data)
    return None


def _replay_payload_missing_fields(task_type: str, payload: dict[str, Any]) -> list[str]:
    if task_type == "airtime":
        missing: list[str] = []
        if payload.get("amount") is None:
            missing.append("amount")
        if not (payload.get("recipient_phone") or payload.get("target_phone")):
            missing.append("phone number")
        return missing
    if task_type == "data":
        missing = []
        if not (payload.get("target_phone") or payload.get("recipient_phone")):
            missing.append("phone number")
        if not (payload.get("amount") is not None or payload.get("plan_code") or payload.get("plan_name")):
            missing.append("data plan or amount")
        return missing

    missing = []
    if payload.get("amount") is None:
        missing.append("amount")
    if payload.get("beneficiary_id"):
        return missing
    if not (payload.get("recipient_account") or payload.get("recipient_account_number")):
        missing.append("recipient account number")
    if not (payload.get("recipient_bank_code") or payload.get("recipient_bank_name")):
        missing.append("recipient bank")
    return missing


def _is_replay_payload_sufficient(task_type: str, payload: dict[str, Any]) -> bool:
    return not _replay_payload_missing_fields(task_type, payload)


def _format_missing_replay_fields(fields: list[str]) -> str:
    unique_fields = list(dict.fromkeys(field for field in fields if field))
    if not unique_fields:
        return "I can resend that, but I need the missing transaction details first."
    if len(unique_fields) == 1:
        return f"I can resend that, but I need the missing {unique_fields[0]} first."
    field_text = ", ".join(unique_fields[:-1]) + f", and {unique_fields[-1]}"
    return f"I can resend that, but I need the missing {field_text} first."


def _normalize_replay_status(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"success", "successful", "completed", "confirmed"}:
        return "success"
    if normalized in {"processing", "pending", "queued"}:
        return "processing"
    if normalized in {"failed", "error"}:
        return "failed"
    return None


def _quoted_seed_task_items(quoted_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(quoted_payload, dict) or not quoted_payload:
        return []
    if isinstance(quoted_payload.get("tasks"), list):
        candidates = [task for task in quoted_payload["tasks"] if isinstance(task, dict)]
    else:
        candidates = [quoted_payload]
    seed_tasks: list[dict[str, Any]] = []
    for candidate in candidates:
        task_type = str(candidate.get("task_type") or "").strip().lower()
        if task_type not in _REPLAY_TASK_TYPES:
            continue
        seed_tasks.append(candidate)
    return seed_tasks


def _scope_requested(interpretation: QuotedReplayInterpretation) -> bool:
    return bool(interpretation.target_statuses or interpretation.target_types or interpretation.target_task_ids)


def _seed_task_matches_scope(task: dict[str, Any], interpretation: QuotedReplayInterpretation) -> bool:
    target_task_ids = {str(task_id).strip() for task_id in interpretation.target_task_ids if str(task_id).strip()}
    target_types = {str(task_type).strip().lower() for task_type in interpretation.target_types}
    target_statuses = {str(status).strip().lower() for status in interpretation.target_statuses}

    if target_task_ids:
        task_ids = {
            str(task.get("task_id") or "").strip(),
            str(task.get("id") or "").strip(),
            str(task.get("transaction_id") or "").strip(),
            str(task.get("idempotency_key") or "").strip(),
        }
        if not task_ids.intersection(target_task_ids):
            return False

    if target_types and str(task.get("task_type") or "").strip().lower() not in target_types:
        return False

    if target_statuses:
        normalized_status = _normalize_replay_status(task.get("final_status")) or "success"
        if normalized_status not in target_statuses:
            return False

    return True


def _select_seed_tasks(
    *,
    quoted_payload: dict[str, Any] | None,
    interpretation: QuotedReplayInterpretation,
) -> list[dict[str, Any]]:
    seed_tasks = _quoted_seed_task_items(quoted_payload)
    if not seed_tasks:
        return []
    if not _scope_requested(interpretation):
        return seed_tasks
    return [task for task in seed_tasks if _seed_task_matches_scope(task, interpretation)]


def _normalize_replay_source_affinity(payload: dict[str, Any]) -> None:
    mode = str(payload.get("source_affinity_mode") or "").strip().lower()
    if mode in {"explicit", "auto"}:
        payload["source_affinity_mode"] = mode
        return

    has_source_reference = any(
        payload.get(key) not in (None, "")
        for key in (
            "source_account_id",
            "source_account_number",
            "source_account_index",
            "source_bank_name",
        )
    )
    payload["source_affinity_mode"] = "explicit" if has_source_reference else "auto"


def _sanitize_replay_task_payload(*, task_type: str, payload: dict[str, Any], text: str) -> dict[str, Any] | None:
    next_payload = {key: value for key, value in payload.items() if value is not None}
    for metadata_key in (
        "task_id",
        "task_ids",
        "task_type",
        "task_types",
        "tasks",
        "final_status",
        "error_message",
        "failure_category",
    ):
        next_payload.pop(metadata_key, None)
    if task_type == "transfer" and not next_payload.get("recipient_account"):
        recipient_account_number = next_payload.get("recipient_account_number")
        if recipient_account_number:
            next_payload["recipient_account"] = recipient_account_number
    if "action" not in next_payload:
        next_payload["action"] = {"transfer": "send_money", "airtime": "buy_airtime", "data": "buy_data"}[task_type]
    next_payload.setdefault("instruction", text)
    next_payload.setdefault("message", text)
    next_payload["skip_extraction"] = True
    _normalize_replay_source_affinity(next_payload)
    confirmation = next_payload.get("confirmation")
    if not isinstance(confirmation, dict):
        confirmation = {}
    confirmation["confirmed"] = False
    next_payload["confirmation"] = confirmation
    next_payload["idempotency_key"] = None
    next_payload["transaction_id"] = None

    if not _is_replay_payload_sufficient(task_type, next_payload):
        return None
    return next_payload


def _build_quoted_replay_execution_updates(
    *,
    state: OrchestratorState,
    text: str,
    interpretation: QuotedReplayInterpretation,
    locale_updates: dict[str, Any],
    quoted_payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    new_tasks: dict[str, TaskSpec] = {}
    wave_ids: list[str] = []
    allocated_ids: set[str] = set()
    missing_fields: list[str] = []

    seed_tasks = _select_seed_tasks(quoted_payload=quoted_payload, interpretation=interpretation)
    use_seed_tasks = bool(seed_tasks and (_scope_requested(interpretation) or not interpretation.tasks))
    task_inputs: list[tuple[str, dict[str, Any]]] = []
    if use_seed_tasks:
        task_inputs = [
            (str(seed_task.get("task_type") or "").strip().lower(), dict(seed_task))
            for seed_task in seed_tasks
        ]
    else:
        task_inputs = [
            (item.task_type, item.payload.model_dump(exclude_none=True))
            for item in interpretation.tasks
        ]

    for task_type, payload in task_inputs:
        sanitized_payload = _sanitize_replay_task_payload(task_type=task_type, payload=payload, text=text)
        if sanitized_payload is None:
            preview_payload = {key: value for key, value in payload.items() if value is not None}
            if task_type == "transfer" and not preview_payload.get("recipient_account"):
                recipient_account_number = preview_payload.get("recipient_account_number")
                if recipient_account_number:
                    preview_payload["recipient_account"] = recipient_account_number
            missing_fields.extend(_replay_payload_missing_fields(task_type, preview_payload))
            continue
        task_id = _next_quoted_replay_task_id(state, task_type, allocated_ids)
        allocated_ids.add(task_id)
        new_tasks[task_id] = TaskSpec(
            id=task_id,
            type=cast(Any, task_type),
            stage=TaskStage.DRAFT,
            payload=sanitized_payload,
        )
        wave_ids.append(task_id)

    if not wave_ids:
        if missing_fields:
            return {
                "final_response": _format_missing_replay_fields(missing_fields),
                "normalized_instruction": text,
                "semantic_path_shape": "quoted_router",
                **locale_updates,
            }
        return None

    logger.info(
        "quoted_replay_shortcut_hit",
        decision=interpretation.decision,
        task_count=len(wave_ids),
    )
    return {
        "tasks": new_tasks,
        "waves": [wave_ids],
        "current_wave_index": 0,
        "normalized_instruction": text,
        "semantic_path_shape": "quoted_router",
        **locale_updates,
    }


def _quoted_replay_clarify_response(interpretation: QuotedReplayInterpretation, locale: str) -> str:
    if interpretation.decision == "execute" and _scope_requested(interpretation):
        return (
            "I couldn't find a quoted transaction matching that replay request."
            if locale == "en"
            else render_message("conversational.clarify", locale)
        )
    return interpretation.clarify_message or (
        "I can resend that, but I need the missing amount, recipient, or source details first."
        if locale == "en"
        else render_message("conversational.clarify", locale)
    )


__all__ = [
    "QUOTED_REPLAY_MIN_CONFIDENCE",
    "_build_quoted_replay_context",
    "_build_quoted_replay_context_with_payload",
    "_build_quoted_replay_execution_updates",
    "_load_quoted_actionable_payload",
    "_quoted_replay_clarify_response",
]
