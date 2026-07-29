"""Typed query-conversation preferences and sparse updates."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

QueryPresentationDetail = Literal["concise", "detailed"]
QueryDefaultShape = Literal["summary", "list"]
QueryRelativePeriodMode = Literal["calendar", "rolling"]
QueryDefaultActivityMeasure = Literal["spending", "income", "cash_flow"]
QueryDefaultStatusInclusion = Literal["settled", "all"]
QueryPreferenceField = Literal[
    "presentation_detail",
    "default_shape",
    "default_account_refs",
    "relative_period_mode",
    "default_activity_measure",
    "default_status_inclusion",
]


class QueryPreferencesV1(BaseModel):
    """Conservative preferences persisted under ``User.extra_data``."""

    model_config = ConfigDict(extra="ignore")

    schema_version: Literal[1] = 1
    presentation_detail: QueryPresentationDetail | None = None
    default_shape: QueryDefaultShape | None = None
    default_account_refs: list[str] = Field(default_factory=list, max_length=5)
    relative_period_mode: QueryRelativePeriodMode | None = None
    default_activity_measure: QueryDefaultActivityMeasure | None = None
    default_status_inclusion: QueryDefaultStatusInclusion | None = None


class QueryPreferenceUpdate(BaseModel):
    """Sparse, explicit mutation requested by the user."""

    model_config = ConfigDict(extra="forbid")

    presentation_detail: QueryPresentationDetail | None = None
    default_shape: QueryDefaultShape | None = None
    default_account_names: list[str] | None = Field(default=None, max_length=5)
    relative_period_mode: QueryRelativePeriodMode | None = None
    default_activity_measure: QueryDefaultActivityMeasure | None = None
    default_status_inclusion: QueryDefaultStatusInclusion | None = None
    clear_fields: list[QueryPreferenceField] = Field(default_factory=list)
    reset_all: bool = False

    @model_validator(mode="after")
    def require_a_change(self) -> QueryPreferenceUpdate:
        supplied = any(
            value is not None
            for value in (
                self.presentation_detail,
                self.default_shape,
                self.default_account_names,
                self.relative_period_mode,
                self.default_activity_measure,
                self.default_status_inclusion,
            )
        )
        if not supplied and not self.clear_fields and not self.reset_all:
            raise ValueError("a query preference update must change or reset at least one field")
        if len(set(self.clear_fields)) != len(self.clear_fields):
            raise ValueError("query preference clear_fields must be unique")
        return self


def query_preferences_from_profile(profile: object) -> QueryPreferencesV1:
    """Load valid preferences from a cached user profile without trusting its JSON."""

    if not isinstance(profile, dict):
        return QueryPreferencesV1()
    extra_data = profile.get("extra_data")
    if not isinstance(extra_data, dict):
        return QueryPreferencesV1()
    raw = extra_data.get("query_preferences")
    if not isinstance(raw, dict):
        return QueryPreferencesV1()
    try:
        return QueryPreferencesV1.model_validate(raw)
    except Exception:
        return QueryPreferencesV1()


__all__ = [
    "QueryDefaultActivityMeasure",
    "QueryDefaultShape",
    "QueryDefaultStatusInclusion",
    "QueryPreferenceField",
    "QueryPreferenceUpdate",
    "QueryPreferencesV1",
    "QueryPresentationDetail",
    "QueryRelativePeriodMode",
    "query_preferences_from_profile",
]
