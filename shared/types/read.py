"""Canonical contracts for read-only banking responses."""

from __future__ import annotations

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

ResponseShape: TypeAlias = Literal[
    "fact_bool",
    "fact_count",
    "fact_value",
    "fact_status",
    "fact_recap",
    "surface_list",
    "surface_detail",
    "surface_paginated",
    "surface_actionable",
]
ReadSubject: TypeAlias = Literal[
    "transaction",
    "balance",
    "linked_account",
    "default_account",
    "beneficiary",
    "schedule",
    "ticket",
    "receipt",
]

_ALLOWED_SHAPES: dict[str, frozenset[str]] = {
    "transaction": frozenset(
        {
            "fact_bool",
            "fact_count",
            "fact_value",
            "fact_status",
            "surface_list",
            "surface_detail",
            "surface_paginated",
            "surface_actionable",
        }
    ),
    "balance": frozenset({"fact_value", "surface_list", "surface_detail"}),
    "linked_account": frozenset(
        {"fact_bool", "fact_count", "fact_status", "surface_list", "surface_detail", "surface_paginated"}
    ),
    "default_account": frozenset({"fact_value", "fact_status", "surface_detail"}),
    "beneficiary": frozenset(
        {"fact_bool", "fact_count", "surface_list", "surface_detail", "surface_paginated"}
    ),
    "schedule": frozenset(
        {"fact_bool", "fact_count", "fact_status", "surface_list", "surface_detail", "surface_paginated"}
    ),
    "ticket": frozenset(
        {"fact_bool", "fact_count", "fact_status", "surface_list", "surface_detail", "surface_paginated"}
    ),
    "receipt": frozenset({"surface_detail", "surface_actionable"}),
}


class ReadSelector(BaseModel):
    """Optional reference to an item in a prior read surface."""

    model_config = ConfigDict(extra="forbid")

    selector: Literal["previous", "index", "label"]
    index: int | None = Field(default=None, ge=1)
    label: str | None = Field(default=None, max_length=120)


class ReadRequest(BaseModel):
    """Canonical description of a read and its requested presentation."""

    model_config = ConfigDict(extra="forbid")

    subject: ReadSubject
    response_shape: ResponseShape
    entity_name: str | None = Field(default=None, max_length=120)
    bank_name: str | None = Field(default=None, max_length=120)
    status: str | None = Field(default=None, max_length=64)
    reference: str | None = Field(default=None, max_length=160)
    selector: ReadSelector | None = None
    offset: int = Field(default=0, ge=0)
    page_size: Literal[5] = 5

    @model_validator(mode="after")
    def validate_subject_shape(self) -> ReadRequest:
        if self.response_shape not in _ALLOWED_SHAPES[self.subject]:
            raise ValueError(f"{self.response_shape!r} is not valid for read subject {self.subject!r}")
        return self


class ReadResult(BaseModel):
    """Safe result metadata used for continuation and observability."""

    model_config = ConfigDict(extra="forbid")

    request: ReadRequest
    total_count: int = Field(default=0, ge=0)
    returned_count: int = Field(default=0, ge=0)
    has_next: bool = False
    has_previous: bool = False

    @model_validator(mode="after")
    def validate_page(self) -> ReadResult:
        if self.returned_count > self.request.page_size:
            raise ValueError("returned_count cannot exceed the canonical page size")
        if self.has_previous != (self.request.offset > 0):
            raise ValueError("has_previous must reflect the request offset")
        return self


def normalize_read_request(
    payload: dict[str, Any],
) -> ReadRequest | None:
    """Validate the canonical nested read contract."""
    raw = payload.get("read_request")
    if isinstance(raw, ReadRequest):
        return raw
    if isinstance(raw, dict):
        try:
            return ReadRequest.model_validate(raw)
        except (TypeError, ValueError):
            return None

    return None


def read_request_payload(request: ReadRequest | None) -> dict[str, Any]:
    """Serialize a canonical request for a runtime task payload."""
    if request is None:
        return {}
    return {"read_request": request.model_dump(mode="json", exclude_none=True)}


__all__ = [
    "ReadRequest",
    "ReadResult",
    "ReadSelector",
    "ReadSubject",
    "ResponseShape",
    "normalize_read_request",
    "read_request_payload",
]
