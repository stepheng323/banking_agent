"""Amount patch builders for pending-action edits."""

from typing import Any

from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_updates import _amount_patch

from .pending_action_data_plan_patches import _data_plan_reset_patch


def _typed_amount_patch(value: Any, *, task_type: str | None = None) -> dict[str, Any]:
    amount = 0.0
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return {}
    if amount <= 0:
        return {}
    if task_type == "data":
        patch: dict[str, Any] = {"confirmation": {"confirmed": False}, "amount": amount}
        patch.update(_data_plan_reset_patch())
        patch["size_preference"] = None
        return patch
    if task_type == "airtime":
        return {"confirmation": {"confirmed": False}, "amount": amount}
    return _amount_patch(amount)


__all__ = ["_typed_amount_patch"]
