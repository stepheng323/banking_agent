"""Narrow LLM contract for amending one pending transfer."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from shared.money import MoneyAmount
from shared.types.amount_mutation import AmountMutation


def _strip_schema_annotations(schema: dict[str, Any]) -> None:
    root_title = schema.get("title")

    def strip(value: object) -> None:
        if isinstance(value, dict):
            value.pop("title", None)
            value.pop("description", None)
            value.pop("default", None)
            for child in value.values():
                strip(child)
        elif isinstance(value, list):
            for child in value:
                strip(child)

    strip(schema)
    if isinstance(root_title, str) and root_title:
        schema["title"] = root_title


class TransferAmendmentFundingSplit(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra=_strip_schema_annotations)

    source: str
    amount: MoneyAmount


class TransferAmendmentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra=_strip_schema_annotations)

    operation: Literal["update", "confirm", "cancel", "unrelated", "unclear"]
    amount_mutation: AmountMutation | None = None
    recipient_name: str | None = None
    recipient_account: str | None = None
    recipient_bank_name: str | None = None
    source_bank_name: str | None = None
    source_account_index: int | None = Field(default=None, ge=1)
    transfer_percentage: float | None = Field(default=None, gt=0, le=100)
    transfer_all: bool | None = None
    source_accounts: list[str] | None = Field(default=None, max_length=2)
    use_dual_accounts: bool | None = None
    explicit_split: list[TransferAmendmentFundingSplit] | None = None
    narration: str | None = None
    acknowledgment: str | None = Field(default=None, max_length=80)
    requires_broad_interpretation: bool = False

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        schema = super().model_json_schema(*args, **kwargs)
        _strip_schema_annotations(schema)
        return schema


__all__ = ["TransferAmendmentFundingSplit", "TransferAmendmentPatch"]
