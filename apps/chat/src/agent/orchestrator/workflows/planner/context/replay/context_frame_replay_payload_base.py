"""Base payload assembly for context-frame transaction replay."""

from typing import Any

from apps.chat.src.agent.orchestrator.context.models import ContextEntity
from apps.chat.src.agent.orchestrator.workflows.planner.context.replay.context_frame_replay_amounts import (
    _replay_amount_override,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.replay.context_frame_replay_modifier_core import (
    _modifier_amount_override,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.replay.context_frame_replay_payload_values import (
    payload_value,
)
from shared.types.planner import ContextFrameReplayModifier


def _replay_action(task_type: str) -> str:
    return {"transfer": "send_money", "airtime": "buy_airtime", "data": "buy_data"}[task_type]


def _base_replay_payload(
    entity: ContextEntity,
    *,
    task_type: str,
    text: str,
    replay_modifier: ContextFrameReplayModifier | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": _replay_action(task_type),
        "instruction": text,
        "message": text,
        "skip_extraction": True,
        "confirmation": {"confirmed": False},
        "idempotency_key": None,
        "transaction_id": None,
    }
    amount = payload_value(entity, "amount")
    if amount is not None:
        payload["amount"] = amount
    amount_override = _replay_amount_override(text)
    if amount_override is None:
        amount_override = _modifier_amount_override(text, replay_modifier)
    if amount_override is not None:
        payload["amount"] = amount_override
        payload["suggested_amount"] = None
        payload["transfer_percentage"] = None
        payload["transfer_all"] = False
        payload["funding_plan"] = None

    for key in ("source_account_id", "source_bank_name", "source_account_number", "source_account_index"):
        value = payload_value(entity, key)
        if value is not None:
            payload[key] = value
    return payload


__all__ = ["_base_replay_payload"]
