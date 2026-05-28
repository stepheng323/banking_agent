"""Sanitization helpers for quoted replay task payloads."""

from typing import Any

from apps.chat.src.agent.orchestrator.workflows.planner.quoted_replay.quoted_replay_modifiers import (
    _modifier_amount_override,
    _modifier_narration_candidate,
)
from apps.chat.src.agent.orchestrator.workflows.planner.quoted_replay.quoted_replay_payload_validation import (
    _is_replay_payload_sufficient,
)
from shared.types.planner import ContextFrameReplayModifier


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


def _sanitize_replay_task_payload(
    *,
    task_type: str,
    payload: dict[str, Any],
    text: str,
    replay_modifier: ContextFrameReplayModifier | None = None,
    source_override: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
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
    amount_override = _modifier_amount_override(text, replay_modifier)
    if amount_override is not None and task_type in {"transfer", "airtime"}:
        next_payload["amount"] = amount_override
    if source_override and task_type in {"transfer", "airtime", "data"}:
        next_payload.update(source_override)
    if task_type == "transfer":
        narration_override = _modifier_narration_candidate(text, replay_modifier)
        if narration_override:
            next_payload["narration"] = narration_override
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


__all__ = [
    "_normalize_replay_source_affinity",
    "_sanitize_replay_task_payload",
]
