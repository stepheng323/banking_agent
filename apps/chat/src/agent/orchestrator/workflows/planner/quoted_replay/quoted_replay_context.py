"""Quoted replay context and actionable payload loading helpers."""

import json
from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_rendering_active import (
    build_quoted_replay_context_from_summary,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.context_summary import (
    get_or_build_turn_context_summary,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)

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


async def _load_quoted_actionable_payload(
    state: OrchestratorState,
    actionable_message_repo: Any | None,
) -> dict[str, Any] | None:
    if actionable_message_repo is None or not state.quoted_message_id:
        return None

    user_id = (state.loaded_context or {}).get("user_id") or state.user_id
    if not user_id:
        logger.info("quoted_replay_actionable_lookup_skipped", reason="missing_user_id")
        return None

    try:
        row = await actionable_message_repo.get_by_channel_message_id_for_user(state.quoted_message_id, str(user_id))
    except Exception as exc:
        logger.warning("quoted_replay_actionable_lookup_failed", error=str(exc))
        return None

    if not row:
        return None

    message_data = row.get("message_data") if isinstance(row, dict) else getattr(row, "message_data", None)
    if isinstance(message_data, dict):
        return dict(message_data)
    return None


__all__ = [
    "QUOTED_REPLAY_PAYLOAD_KEYS",
    "QUOTED_REPLAY_PAYLOAD_PREVIEW_MAX_CHARS",
    "QUOTED_REPLAY_TASK_PREVIEW_LIMIT",
    "_build_quoted_replay_context",
    "_build_quoted_replay_context_with_payload",
    "_load_quoted_actionable_payload",
]
