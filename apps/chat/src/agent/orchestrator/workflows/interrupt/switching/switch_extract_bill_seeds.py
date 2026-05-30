"""Airtime and data seed payload construction for interrupt switches."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.switching.switch_extract_actions import (
    _infer_transfer_switch_action,
    _map_schedule_action_for_domain,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.switching.switch_extract_extractor import (
    _extract_interrupt_switch_entities,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.switching.switch_extract_values import _coerce_money
from shared.types.planner import TaskParameters


async def _seed_airtime_switch_payload(
    *,
    state: OrchestratorState,
    interrupt: Any,
    text: str,
    services: dict[str, Any],
) -> tuple[TaskParameters, dict[str, Any], str, bool]:
    parameters = TaskParameters()
    payload_seed: dict[str, Any] = {}
    entities, features, _acknowledgment = await _extract_interrupt_switch_entities(
        state=state,
        interrupt=interrupt,
        text=text,
        services=services,
        target_intent="airtime",
    )

    amount = _coerce_money(entities.get("amount"))
    if amount is not None:
        parameters.amount = amount

    recipient_phone = str(entities.get("recipient_phone") or "").strip()
    if recipient_phone:
        parameters.recipient_phone = recipient_phone
        parameters.phone = recipient_phone

    recipient_name = str(entities.get("recipient_name") or "").strip()
    if recipient_name:
        parameters.recipient_name = recipient_name

    if entities.get("is_self") is not None:
        parameters.is_self = bool(entities.get("is_self"))
    elif not recipient_phone and not recipient_name:
        parameters.is_self = True

    source_bank_name = str(entities.get("source_bank_name") or "").strip()
    if source_bank_name:
        parameters.source_bank_name = source_bank_name

    source_account_index = entities.get("source_account_index")
    if isinstance(source_account_index, int):
        parameters.source_account_index = source_account_index

    narration = str(entities.get("narration") or "").strip()
    if narration:
        parameters.narration = narration

    network = str(entities.get("network") or "").strip()
    if network:
        payload_seed["network"] = network

    action = _map_schedule_action_for_domain(_infer_transfer_switch_action(text, features), "airtime")
    if action == "send_money":
        action = "buy_airtime"
    preseeded = bool(features or entities or payload_seed)
    return parameters, payload_seed, action, preseeded


async def _seed_data_switch_payload(
    *,
    state: OrchestratorState,
    interrupt: Any,
    text: str,
    services: dict[str, Any],
) -> tuple[TaskParameters, dict[str, Any], str, bool]:
    parameters = TaskParameters()
    payload_seed: dict[str, Any] = {}
    entities, features, _acknowledgment = await _extract_interrupt_switch_entities(
        state=state,
        interrupt=interrupt,
        text=text,
        services=services,
        target_intent="data",
    )

    budget = _coerce_money(entities.get("budget"))
    if budget is not None:
        parameters.amount = budget

    recipient_phone = str(entities.get("recipient_phone") or "").strip()
    if recipient_phone:
        parameters.recipient_phone = recipient_phone
        payload_seed["target_phone"] = recipient_phone

    network = str(entities.get("network") or "").strip()
    if network:
        payload_seed["network"] = network

    plan_name = str(entities.get("size_preference") or "").strip()
    if plan_name:
        parameters.plan = plan_name
        payload_seed["plan_name"] = plan_name

    if entities.get("is_self") is not None:
        parameters.is_self = bool(entities.get("is_self"))

    recipient_name = str(entities.get("recipient_name") or "").strip()
    if recipient_name:
        parameters.recipient_name = recipient_name

    action = _map_schedule_action_for_domain(_infer_transfer_switch_action(text, features), "data")
    if action == "send_money":
        action = "buy_data"
    preseeded = bool(features or entities or payload_seed)
    return parameters, payload_seed, action, preseeded


__all__ = ["_seed_airtime_switch_payload", "_seed_data_switch_payload"]
