"""Quoted replay planner helper functions."""

import re
from typing import Any, cast

from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.core.src.agent.orchestrator.models.state import OrchestratorState
from apps.core.src.agent.orchestrator.nodes.planner_context import (
    _compact_payload_for_prompt,
    build_quoted_replay_context_from_summary,
    get_or_build_turn_context_summary,
)
from shared.i18n import render_message
from shared.types.quoted_replay import QuotedReplayInterpretation
from shared.utils.logging import get_logger

logger = get_logger(__name__)

QUOTED_REPLAY_MIN_CONFIDENCE = 0.75
_QUOTED_REPLAY_DIRECT_PHRASES = {
    "again",
    "send again",
    "repeat",
    "retry",
    "same",
    "do same",
    "run again",
}
_QUOTED_REPLAY_AMOUNT_CAPTURE_PATTERNS = (
    re.compile(r"^(?:again|same|repeat|retry)(?:\s+but)?\s+(?P<amount>.+)$", re.IGNORECASE),
    re.compile(r"^change\s+amount\s+to\s+(?P<amount>.+)$", re.IGNORECASE),
    re.compile(r"^same\s+but\s+(?P<amount>.+)$", re.IGNORECASE),
    re.compile(r"^again\s+but\s+(?P<amount>.+)$", re.IGNORECASE),
)
_QUOTED_REPLAY_AMOUNT_RE = re.compile(
    r"^(?:₦|ngn)?\s*(?P<number>\d[\d,]*(?:\.\d+)?)\s*(?P<suffix>[kKmM])?$",
    re.IGNORECASE,
)


def _build_quoted_replay_context(state: OrchestratorState) -> str:
    summary, _ = get_or_build_turn_context_summary(state, path_label="planner_path")
    return build_quoted_replay_context_from_summary(
        summary,
        quoted_message_id=state.quoted_message_id,
        has_quote=state.has_quote,
    )


def _build_quoted_replay_context_with_payload(state: OrchestratorState, quoted_payload: dict[str, Any]) -> str:
    payload_preview = _compact_payload_for_prompt(quoted_payload)
    summary, _ = get_or_build_turn_context_summary(state, path_label="planner_path")
    return build_quoted_replay_context_from_summary(
        summary,
        quoted_message_id=state.quoted_message_id,
        has_quote=state.has_quote,
        quoted_payload_preview=payload_preview,
    )


def _normalize_replay_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower()).strip('.,!?;:"`~()[]{}')


def _infer_quoted_replay_task_type(quoted_payload: dict[str, Any]) -> str | None:
    task_type = str(quoted_payload.get("task_type") or "").strip().lower()
    if task_type in {"transfer", "airtime", "data"}:
        return task_type
    action = str(quoted_payload.get("action") or "").strip().lower()
    action_map = {
        "send_money": "transfer",
        "buy_airtime": "airtime",
        "buy_data": "data",
    }
    return action_map.get(action)


def _parse_quoted_replay_amount(value: str) -> float | int | None:
    match = _QUOTED_REPLAY_AMOUNT_RE.match(value.strip())
    if not match:
        return None
    try:
        amount = float(match.group("number").replace(",", ""))
    except ValueError:
        return None
    suffix = (match.group("suffix") or "").lower()
    if suffix == "k":
        amount *= 1000
    elif suffix == "m":
        amount *= 1_000_000
    return int(amount) if amount.is_integer() else amount


def _extract_quoted_replay_amount_delta(normalized_text: str) -> float | int | None:
    for pattern in _QUOTED_REPLAY_AMOUNT_CAPTURE_PATTERNS:
        match = pattern.match(normalized_text)
        if not match:
            continue
        amount = _parse_quoted_replay_amount(match.group("amount"))
        if amount is not None:
            return amount
    return None


def _build_deterministic_quoted_replay_updates(
    *,
    state: OrchestratorState,
    text: str,
    quoted_payload: dict[str, Any] | None,
    locale_updates: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(quoted_payload, dict) or not quoted_payload:
        return None

    normalized_text = _normalize_replay_text(text)
    if not normalized_text:
        return None

    task_type = _infer_quoted_replay_task_type(quoted_payload)
    if task_type is None:
        return None

    next_payload = dict(quoted_payload)
    next_payload.pop("task_type", None)

    if normalized_text in _QUOTED_REPLAY_DIRECT_PHRASES:
        pass
    else:
        amount_delta = _extract_quoted_replay_amount_delta(normalized_text)
        if amount_delta is None:
            return None
        # Keep amount-only patching narrow. Data plans with explicit plan codes/names
        # remain on the quoted-replay LLM path.
        if task_type == "data" and (next_payload.get("plan_code") or next_payload.get("plan_name")):
            return None
        if next_payload.get("amount") is None:
            return None
        next_payload["amount"] = amount_delta

    sanitized_payload = _sanitize_replay_task_payload(task_type=task_type, payload=next_payload, text=text)
    if sanitized_payload is None:
        return None

    task_id = _next_quoted_replay_task_id(state, task_type)
    logger.info("quoted_replay_shortcut_hit", decision="deterministic_execute", task_count=1)
    return {
        "tasks": {
            task_id: TaskSpec(
                id=task_id,
                type=cast(Any, task_type),
                stage=TaskStage.DRAFT,
                payload=sanitized_payload,
            )
        },
        "waves": [[task_id]],
        "current_wave_index": 0,
        "normalized_instruction": text,
        "semantic_path_shape": "quoted_deterministic",
        **locale_updates,
    }


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
    next_payload = dict(payload)
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
    "_build_deterministic_quoted_replay_updates",
    "_build_quoted_replay_execution_updates",
    "_load_quoted_actionable_payload",
    "_quoted_replay_clarify_response",
]
