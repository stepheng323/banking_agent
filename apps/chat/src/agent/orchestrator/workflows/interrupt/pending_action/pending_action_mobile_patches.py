"""Mobile transaction patch builders for pending-action edits."""

from typing import Any

from apps.chat.src.agent.orchestrator.workflows.interrupt.pending_action.pending_action_data_plan_patches import (
    _data_plan_reset_patch,
)
from shared.utils.network_utils import (
    normalize_network_name,
    normalize_nigerian_phone,
    resolve_network_from_phone,
)


def _phone_patch(
    task_type: str,
    value: Any,
    *,
    payload: dict[str, Any] | None = None,
    user_phone: str | None = None,
) -> dict[str, Any] | None:
    phone = normalize_nigerian_phone(str(value or "").strip()) or str(value or "").strip()
    if not phone:
        return None
    normalized_user_phone = normalize_nigerian_phone(user_phone or "")
    is_self = bool(normalized_user_phone and phone == normalized_user_phone)
    if task_type == "airtime":
        patch: dict[str, Any] = {
            "confirmation": {"confirmed": False},
            "recipient_phone": phone,
            "phone": phone,
            "recipient_name": None,
            "beneficiary_id": None,
            "is_self": is_self,
        }
        if inferred_network := resolve_network_from_phone(phone):
            patch["network"] = inferred_network
        return patch
    if task_type == "data":
        data_patch: dict[str, Any] = {
            "confirmation": {"confirmed": False},
            "target_phone": phone,
            "phone": phone,
            "recipient_name": None,
            "beneficiary_id": None,
            "is_self": is_self,
        }
        inferred_network = resolve_network_from_phone(phone)
        current_network = normalize_network_name((payload or {}).get("network")) if payload else None
        if inferred_network and inferred_network != current_network:
            data_patch["network"] = inferred_network
            data_patch.update(_data_plan_reset_patch())
        return data_patch
    return None


def _network_patch(
    task_type: str,
    value: Any,
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    network = str(value or "").strip()
    if not network:
        return None
    patch: dict[str, Any] = {"confirmation": {"confirmed": False}, "network": network}
    normalized_network = normalize_network_name(network)
    if task_type == "airtime":
        raw_phone = (payload or {}).get("recipient_phone") or (payload or {}).get("phone")
        current_phone = normalize_nigerian_phone(str(raw_phone or ""))
        inferred_network = resolve_network_from_phone(current_phone or "")
        if normalized_network and inferred_network and inferred_network != normalized_network:
            patch.update(
                {
                    "recipient_phone": None,
                    "phone": None,
                    "recipient_name": None,
                    "beneficiary_id": None,
                    "is_self": False,
                }
            )
    if task_type == "data":
        raw_phone = (payload or {}).get("target_phone") or (payload or {}).get("phone")
        current_phone = normalize_nigerian_phone(str(raw_phone or ""))
        inferred_network = resolve_network_from_phone(current_phone or "")
        if normalized_network and inferred_network and inferred_network != normalized_network:
            patch.update(
                {
                    "target_phone": None,
                    "phone": None,
                    "recipient_name": None,
                    "beneficiary_id": None,
                    "is_self": False,
                }
            )
        patch.update(_data_plan_reset_patch())
    return patch


__all__ = [
    "_network_patch",
    "_phone_patch",
]
