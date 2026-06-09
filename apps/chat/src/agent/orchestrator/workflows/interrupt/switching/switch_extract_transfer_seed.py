"""Transfer seed payload construction for interrupt transaction switches."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.switching.switch_extract_actions import (
    _infer_transfer_switch_action,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.switching.switch_extract_extractor import (
    _extract_interrupt_switch_entities,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.switching.switch_extract_values import _coerce_money
from apps.chat.src.agent.orchestrator.workflows.services import OrchestrationServices
from shared.money import MoneyAmount
from shared.types.planner import RecipientAllocation, TransferTaskParameters


def _apply_transfer_source_account_entities(parameters: TransferTaskParameters, entities: dict[str, Any]) -> None:
    source_bank_name = str(entities.get("source_bank_name") or "").strip()
    if source_bank_name:
        parameters.source_bank_name = source_bank_name

    source_account_index = entities.get("source_account_index")
    if isinstance(source_account_index, int):
        parameters.source_account_index = source_account_index

    source_accounts = entities.get("source_accounts")
    if isinstance(source_accounts, list):
        normalized_accounts = [str(item).strip() for item in source_accounts if str(item).strip()]
        if normalized_accounts:
            parameters.source_accounts = normalized_accounts

    if entities.get("use_dual_accounts") is not None:
        parameters.use_dual_accounts = bool(entities.get("use_dual_accounts"))


def _apply_transfer_split_entities(parameters: TransferTaskParameters, entities: dict[str, Any]) -> None:
    explicit_split = entities.get("explicit_split")
    if isinstance(explicit_split, dict):
        normalized_split: dict[str, MoneyAmount] = {}
        for key, value in explicit_split.items():
            amount_value = _coerce_money(value)
            if amount_value is None:
                continue
            normalized_key = str(key).strip()
            if not normalized_key:
                continue
            normalized_split[normalized_key] = amount_value
        if normalized_split:
            parameters.explicit_split = normalized_split

    recipient_allocations = entities.get("recipient_allocations")
    if isinstance(recipient_allocations, list):
        normalized_allocations: list[RecipientAllocation] = []
        for item in recipient_allocations:
            if not isinstance(item, dict):
                continue
            recipient_name = str(item.get("recipient_name") or "").strip()
            amount_value = _coerce_money(item.get("amount"))
            if not recipient_name or amount_value is None:
                continue
            normalized_allocations.append(RecipientAllocation(recipient_name=recipient_name, amount=amount_value))
        if normalized_allocations:
            parameters.recipient_allocations = normalized_allocations


async def _seed_transfer_switch_payload(
    *,
    state: OrchestratorState,
    interrupt: Any,
    text: str,
    services: OrchestrationServices,
) -> tuple[TransferTaskParameters, dict[str, Any], str, bool]:
    parameters = TransferTaskParameters()
    payload_seed: dict[str, Any] = {}
    action = _infer_transfer_switch_action(text, set())
    entities, features, acknowledgment = await _extract_interrupt_switch_entities(
        state=state,
        interrupt=interrupt,
        text=text,
        services=services,
        target_intent="transfer",
    )
    action = _infer_transfer_switch_action(text, features)

    recipient_name = str(entities.get("recipient_name") or "").strip()
    if recipient_name:
        parameters.recipient = recipient_name
        parameters.recipient_name = recipient_name

    recipient_account = str(entities.get("recipient_account") or "").strip()
    if recipient_account:
        parameters.recipient_account = recipient_account
        payload_seed["recipient_account"] = recipient_account

    bank_name = str(entities.get("bank_name") or "").strip()
    if bank_name:
        parameters.bank_name = bank_name
        payload_seed["recipient_bank_name"] = bank_name

    amount = _coerce_money(entities.get("amount"))
    if amount is not None:
        parameters.amount = amount

    narration = str(entities.get("narration") or "").strip()
    if narration:
        parameters.narration = narration

    _apply_transfer_source_account_entities(parameters, entities)
    _apply_transfer_split_entities(parameters, entities)

    recipient_bank_code = str(entities.get("bank_code") or "").strip()
    if recipient_bank_code:
        payload_seed["recipient_bank_code"] = recipient_bank_code

    source_account_id = str(entities.get("source_account_id") or "").strip()
    if source_account_id:
        payload_seed["source_account_id"] = source_account_id

    if acknowledgment:
        payload_seed["transition_acknowledgment"] = acknowledgment

    preseeded = bool(features or entities or payload_seed)
    return parameters, payload_seed, action, preseeded


__all__ = ["_seed_transfer_switch_payload"]
