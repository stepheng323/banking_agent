"""Typed, composable edits to an already-known transaction amount."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.money import MoneyAmount

AmountMutationOperation = Literal["set", "add", "subtract", "multiply"]


class AmountMutationStep(BaseModel):
    """One ordered arithmetic operation against a pending transaction amount."""

    model_config = ConfigDict(extra="forbid")

    operation: AmountMutationOperation
    amount: MoneyAmount | None = Field(
        default=None,
        description="Naira amount for set, add, or subtract operations",
    )
    factor: Decimal | None = Field(
        default=None,
        description="Positive multiplier for multiply operations, such as 2 for double or 0.5 for half",
    )

    @model_validator(mode="after")
    def validate_operand(self) -> AmountMutationStep:
        if self.operation == "multiply":
            if self.factor is None or self.factor <= 0 or self.amount is not None:
                raise ValueError("multiply requires a positive factor and no amount")
        elif self.amount is None or self.amount <= 0 or self.factor is not None:
            raise ValueError(f"{self.operation} requires a positive amount and no factor")
        return self


class AmountMutation(BaseModel):
    """A bounded sequence of edits relative to the current pending amount.

    Balance-derived requests (for example, "send half of what I have") remain
    transfer-percentage intent and are deliberately not represented here.
    """

    model_config = ConfigDict(extra="forbid")

    basis: Literal["current_pending_amount"] = "current_pending_amount"
    steps: list[AmountMutationStep] = Field(min_length=1, max_length=3)


def set_amount_mutation(value: MoneyAmount) -> AmountMutation:
    """Build the canonical absolute-amount form for compatibility callers."""
    return AmountMutation(steps=[AmountMutationStep(operation="set", amount=value)])


__all__ = [
    "AmountMutation",
    "AmountMutationOperation",
    "AmountMutationStep",
    "set_amount_mutation",
]
