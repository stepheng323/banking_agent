"""Task-target matching for confirmation interrupt text."""

import re

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_text import (
    _digits_only,
    _normalize_recipient_match_text,
)


def _message_targets_transfer_task(message_text: str, task: TaskSpec) -> bool:
    payload = task.payload if isinstance(task.payload, dict) else {}
    normalized_message = _normalize_recipient_match_text(message_text)
    if not normalized_message:
        return False

    message_digits = _digits_only(message_text)
    recipient_account = _digits_only(str(payload.get("recipient_account") or ""))
    if len(recipient_account) >= 10 and recipient_account in message_digits:
        return True

    recipient_names = [
        str(payload.get("recipient_name") or "").strip(),
        str(payload.get("recipient_resolved_name") or "").strip(),
    ]
    for candidate in recipient_names:
        normalized_candidate = _normalize_recipient_match_text(candidate)
        if not normalized_candidate:
            continue
        if re.search(rf"\b{re.escape(normalized_candidate)}\b", normalized_message):
            return True

        tokens = [token for token in normalized_candidate.split() if len(token) >= 3]
        if len(tokens) < 2:
            continue
        token_hits = sum(1 for token in tokens if re.search(rf"\b{re.escape(token)}\b", normalized_message))
        if token_hits >= 2:
            return True

    return False


def _message_targets_airtime_task(message_text: str, task: TaskSpec) -> bool:
    payload = task.payload if isinstance(task.payload, dict) else {}
    normalized_message = _normalize_recipient_match_text(message_text)
    if not normalized_message:
        return False

    if re.search(r"\b(airtime|recharge|top up|topup)\b", normalized_message):
        return True

    message_digits = _digits_only(message_text)
    recipient_phone = _digits_only(str(payload.get("recipient_phone") or ""))
    if len(recipient_phone) >= 10 and recipient_phone in message_digits:
        return True

    network = _normalize_recipient_match_text(str(payload.get("network") or ""))
    if network and re.search(rf"\b{re.escape(network)}\b", normalized_message):
        return True

    recipient_name = _normalize_recipient_match_text(str(payload.get("recipient_name") or ""))
    if recipient_name and re.search(rf"\b{re.escape(recipient_name)}\b", normalized_message):
        return True

    return False


def _message_targets_data_task(message_text: str, task: TaskSpec) -> bool:
    payload = task.payload if isinstance(task.payload, dict) else {}
    normalized_message = _normalize_recipient_match_text(message_text)
    if not normalized_message:
        return False

    if re.search(r"\b(data|bundle|plan|mb|gb)\b", normalized_message):
        return True

    message_digits = _digits_only(message_text)
    target_phone = _digits_only(str(payload.get("target_phone") or ""))
    if len(target_phone) >= 10 and target_phone in message_digits:
        return True

    network = _normalize_recipient_match_text(str(payload.get("network") or ""))
    if network and re.search(rf"\b{re.escape(network)}\b", normalized_message):
        return True

    plan_name = _normalize_recipient_match_text(str(payload.get("plan_name") or ""))
    if plan_name and re.search(rf"\b{re.escape(plan_name)}\b", normalized_message):
        return True

    return False


def _message_targets_confirmation_task(message_text: str, task: TaskSpec) -> bool:
    if task.type == "transfer":
        return _message_targets_transfer_task(message_text, task)
    if task.type == "airtime":
        return _message_targets_airtime_task(message_text, task)
    if task.type == "data":
        return _message_targets_data_task(message_text, task)
    return False


__all__ = [
    "_message_targets_airtime_task",
    "_message_targets_confirmation_task",
    "_message_targets_data_task",
    "_message_targets_transfer_task",
]
