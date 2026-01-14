"""Relative intent models for amount resolution.

Separates relative references (half, double, tithe) from direct amounts,
enabling cleaner state management and more deterministic resolution.
"""

from typing import Literal

from pydantic import BaseModel, Field

RelativeAmountType = Literal[
    "percentage",
    "all",
    "multiplier",
]


class RelativeAmount(BaseModel):
    """Represents a relative amount reference instead of an absolute value."""

    type: RelativeAmountType = Field(description="Type of relative reference")
    value: float | None = Field(
        default=None,
        description="Value for percentage (50 for half) or multiplier (2 for double)",
    )
    source_bank: str | None = Field(
        default=None,
        description="Specific source bank for the calculation if mentioned",
    )

    @classmethod
    def percentage(cls, pct: float, source_bank: str | None = None) -> "RelativeAmount":
        """Create a percentage-based relative amount."""
        return cls(type="percentage", value=pct, source_bank=source_bank)

    @classmethod
    def all(cls, source_bank: str | None = None) -> "RelativeAmount":
        """Create a 'transfer all' relative amount."""
        return cls(type="all", value=100, source_bank=source_bank)

    @classmethod
    def multiplier(cls, mult: float) -> "RelativeAmount":
        """Create a multiplier-based relative amount."""
        return cls(type="multiplier", value=mult)


RELATIVE_PATTERNS = {
    "half": RelativeAmount.percentage(50),
    "quarter": RelativeAmount.percentage(25),
    "third": RelativeAmount.percentage(33.33),
    "tithe": RelativeAmount.percentage(10),
    "zakat": RelativeAmount.percentage(2.5),
    "everything": RelativeAmount.all(),
    "all": RelativeAmount.all(),
    "entire balance": RelativeAmount.all(),
    "empty": RelativeAmount.all(),
    "double": RelativeAmount.multiplier(2),
    "triple": RelativeAmount.multiplier(3),
}
