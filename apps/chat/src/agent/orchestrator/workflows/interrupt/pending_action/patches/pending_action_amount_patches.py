"""Amount patch builders for pending-action edits."""

from typing import Any

from apps.chat.src.agent.orchestrator.workflows.interrupt.confirmation.confirmation_updates import _amount_patch
from shared.money import to_naira
from shared.money_mutations import AmountMutationEvaluationError, evaluate_amount_mutation
from shared.types.amount_mutation import AmountMutation, set_amount_mutation

from .pending_action_data_plan_patches import _data_plan_reset_patch


def _typed_amount_patch(
    mutation: AmountMutation | None,
    *,
    task_type: str | None = None,
    current_amount: Any = None,
    legacy_amount: Any = None,
) -> dict[str, Any]:
    if mutation is None:
        parsed_legacy = to_naira(legacy_amount)
        if parsed_legacy is None:
            return {}
        mutation = set_amount_mutation(parsed_legacy)
    elif isinstance(mutation, dict):
        try:
            mutation = AmountMutation.model_validate(mutation)
        except ValueError:
            return {}
    try:
        # Pending task payloads currently use native numeric values. Keep Decimal
        # arithmetic in the shared evaluator, then preserve that established shape.
        amount = float(evaluate_amount_mutation(current_amount, mutation))
    except AmountMutationEvaluationError:
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
