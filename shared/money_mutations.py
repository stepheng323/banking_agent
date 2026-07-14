"""Deterministic evaluation of typed pending-amount mutations."""

from __future__ import annotations

from decimal import Decimal

from shared.money import MoneyAmount, quantize_naira, to_naira
from shared.types.amount_mutation import AmountMutation


class AmountMutationEvaluationError(ValueError):
    """Raised when a typed amount mutation cannot be applied safely."""


def evaluate_amount_mutation(
    current_amount: object,
    mutation: AmountMutation,
) -> MoneyAmount:
    """Apply mutation steps in order, rejecting an absent or non-positive result."""
    amount = to_naira(current_amount)

    for step in mutation.steps:
        if step.operation == "set":
            amount = to_naira(step.amount)
        else:
            if amount is None or amount <= 0:
                raise AmountMutationEvaluationError("relative mutation requires a positive pending amount")
            if step.operation == "add":
                operand = to_naira(step.amount)
                if operand is None:
                    raise AmountMutationEvaluationError("add requires a valid amount")
                amount = amount + operand
            elif step.operation == "subtract":
                operand = to_naira(step.amount)
                if operand is None:
                    raise AmountMutationEvaluationError("subtract requires a valid amount")
                amount = amount - operand
            elif step.operation == "multiply":
                if step.factor is None:
                    raise AmountMutationEvaluationError("multiply requires a valid factor")
                amount = amount * Decimal(step.factor)

        if amount is None or amount <= 0:
            raise AmountMutationEvaluationError("amount mutation must produce a positive amount")
        amount = quantize_naira(amount)

    if amount is None:
        raise AmountMutationEvaluationError("amount mutation did not produce an amount")
    return amount


__all__ = ["AmountMutationEvaluationError", "evaluate_amount_mutation"]
