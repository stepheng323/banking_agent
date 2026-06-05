"""Transaction-specific context-frame replay payload builders."""

from typing import Any

from apps.chat.src.agent.orchestrator.context.models import ContextEntity
from apps.chat.src.agent.orchestrator.workflows.planner.context.replay.context_frame_replay_payload_base import (
    _base_replay_payload,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.replay.context_frame_replay_payload_values import (
    payload_value,
    transaction_task_type,
)
from shared.types.planner import ContextFrameReplayModifier


def _transfer_replay_payload(entity: ContextEntity, payload: dict[str, Any]) -> dict[str, Any] | None:
    for key in (
        "beneficiary_id",
        "recipient_name",
        "recipient_resolved_name",
        "recipient_account",
        "recipient_bank_name",
        "recipient_bank_code",
        "narration",
    ):
        value = payload_value(entity, key)
        if value is not None:
            payload[key] = value
    if payload.get("amount") is None:
        return None
    if not any(payload.get(key) for key in ("beneficiary_id", "recipient_account", "recipient_name")):
        return None
    return payload


def _airtime_replay_payload(entity: ContextEntity, payload: dict[str, Any]) -> dict[str, Any] | None:
    phone = payload_value(entity, "recipient_phone", "phone_number", "phone", "target_phone")
    network = payload_value(entity, "network")
    if payload.get("amount") is None or not phone:
        return None
    payload["recipient_phone"] = phone
    payload["phone_number"] = phone
    if network:
        payload["network"] = network
    return payload


def _data_replay_payload(entity: ContextEntity, payload: dict[str, Any]) -> dict[str, Any] | None:
    phone = payload_value(entity, "target_phone", "recipient_phone", "phone_number", "phone")
    if not phone:
        return None
    payload["target_phone"] = phone
    for key in ("network", "plan_code", "plan_name"):
        value = payload_value(entity, key)
        if value is not None:
            payload[key] = value
    if payload.get("amount") is None and not (payload.get("plan_code") or payload.get("plan_name")):
        return None
    return payload


def replay_payload_for_entity(
    entity: ContextEntity,
    *,
    text: str,
    replay_modifier: ContextFrameReplayModifier | None,
) -> tuple[str, dict[str, Any]] | None:
    task_type = transaction_task_type(entity)
    if task_type not in {"transfer", "airtime", "data"}:
        return None

    payload = _base_replay_payload(entity, task_type=task_type, text=text, replay_modifier=replay_modifier)
    replay_payload: dict[str, Any] | None
    if task_type == "transfer":
        replay_payload = _transfer_replay_payload(entity, payload)
    elif task_type == "airtime":
        replay_payload = _airtime_replay_payload(entity, payload)
    else:
        replay_payload = _data_replay_payload(entity, payload)
    return (task_type, replay_payload) if replay_payload is not None else None


__all__ = ["replay_payload_for_entity"]
