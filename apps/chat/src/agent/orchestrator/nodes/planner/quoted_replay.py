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
    "recipient_bank_code",
    "recipient_bank_name",
    "source_bank_name",
    "source_account_id",
    "source_account_number",
    "narration",
    "network",
    "plan_code",
    "plan_name",
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


def _is_replay_payload_sufficient(task_type: str, payload: dict[str, Any]) -> bool:
    if task_type == "airtime":
        return bool(
            payload.get("amount") is not None and (payload.get("recipient_phone") or payload.get("target_phone"))
        )
    if task_type == "data":
        return bool(
            (payload.get("target_phone") or payload.get("recipient_phone"))
            and (payload.get("amount") is not None or payload.get("plan_code") or payload.get("plan_name"))
        )
    return bool(
        payload.get("amount") is not None
        and (
            payload.get("beneficiary_id")
            or payload.get("recipient_account")
            or payload.get("recipient_name")
            or payload.get("recipient_phone")
        )
    )


def _sanitize_replay_task_payload(*, task_type: str, payload: dict[str, Any], text: str) -> dict[str, Any] | None:
    next_payload = {key: value for key, value in payload.items() if value is not None}
    for metadata_key in ("task_id", "task_ids", "task_type", "task_types", "tasks"):
        next_payload.pop(metadata_key, None)
    if "action" not in next_payload:
        next_payload["action"] = {"transfer": "send_money", "airtime": "buy_airtime", "data": "buy_data"}[task_type]
    next_payload.setdefault("instruction", text)
    next_payload.setdefault("message", text)
    next_payload["skip_extraction"] = True
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
) -> dict[str, Any] | None:
    new_tasks: dict[str, TaskSpec] = {}
    wave_ids: list[str] = []
    allocated_ids: set[str] = set()
    for item in interpretation.tasks:
        task_type = item.task_type
        payload = item.payload.model_dump(exclude_none=True)
        sanitized_payload = _sanitize_replay_task_payload(task_type=task_type, payload=payload, text=text)
        if sanitized_payload is None:
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
    return interpretation.clarify_message or render_message("conversational.clarify", locale)


__all__ = [
    "QUOTED_REPLAY_MIN_CONFIDENCE",
    "_build_quoted_replay_context",
    "_build_quoted_replay_context_with_payload",
    "_build_quoted_replay_execution_updates",
    "_load_quoted_actionable_payload",
    "_quoted_replay_clarify_response",
]
