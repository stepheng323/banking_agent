"""Data plan models for data subscription service."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DataPlan(BaseModel):
    """Represents a data plan from a provider."""

    item_code: str = Field(..., description="Provider's item code for the plan")
    biller_code: str = Field(..., description="Provider's biller code for the network")
    name: str = Field(..., description="Plan name (e.g., 'MTN 1GB 30 Days')")
    network: str = Field(..., description="Network provider (MTN, AIRTEL, GLO, 9MOBILE)")
    amount: int = Field(..., description="Price in Naira")

    size_gb: float | None = Field(None, description="Data size in GB")
    validity_days: int | None = Field(None, description="Validity period in days")
    tags: list[str] = Field(default_factory=list, description="Catalog-derived plan tags")
    raw_metadata: dict[str, str | int | float | bool] = Field(
        default_factory=dict,
        description="Safe provider catalog metadata for diagnostics",
    )

    model_config = ConfigDict(frozen=True)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class DataPurchaseDraft(BaseModel):
    """Draft for a pending data purchase."""

    user_id: str
    target_phone: str
    network: str
    plan: DataPlan
    source: Literal["self", "other"] = "self"
    created_at: datetime = Field(default_factory=_utc_now)
    expires_at: datetime

    @property
    def is_expired(self) -> bool:
        return datetime.now(UTC) > self.expires_at


class DataPurchaseResult(BaseModel):
    """Result of a data purchase attempt."""

    success: bool
    transaction_id: str | None = None
    message: str
    plan: DataPlan | None = None
    recipient_phone: str | None = None
    error: str | None = None
