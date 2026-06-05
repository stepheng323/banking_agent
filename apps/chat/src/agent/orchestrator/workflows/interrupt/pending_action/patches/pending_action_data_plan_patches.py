"""Data-plan patch builders for pending-action edits."""

from typing import Any


def _data_plan_reset_patch() -> dict[str, Any]:
    return {
        "plan_code": None,
        "plan_name": None,
        "biller_code": None,
        "plan_size_gb": None,
        "plan_validity_days": None,
        "plan_tags": [],
        "data_plan_candidates": [],
        "show_plan_options": False,
        "data_plan_exclude_codes": [],
        "catalog_cache_stale": False,
    }


def _data_plan_preference_patch(field: str, value: Any, payload: dict[str, Any]) -> dict[str, Any] | None:
    if value in (None, "") and field != "show_options":
        return None

    patch: dict[str, Any] = {"confirmation": {"confirmed": False}}
    patch.update(_data_plan_reset_patch())

    if field == "show_options":
        if value is False:
            return None
        current_plan_code = str(payload.get("plan_code") or "").strip()
        patch["show_plan_options"] = True
        patch["data_plan_exclude_codes"] = [current_plan_code] if current_plan_code else []
        return patch

    normalized_value = str(value).strip()
    if not normalized_value:
        return None
    patch[field] = normalized_value
    if field == "selection_preference" and normalized_value.lower() in {
        "show_options",
        "options",
        "alternatives",
    }:
        current_plan_code = str(payload.get("plan_code") or "").strip()
        patch["show_plan_options"] = True
        patch["data_plan_exclude_codes"] = [current_plan_code] if current_plan_code else []
    return patch


__all__ = [
    "_data_plan_preference_patch",
    "_data_plan_reset_patch",
]
