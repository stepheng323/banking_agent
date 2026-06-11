"""Funding plan validation helpers shared by transfer stages."""

from __future__ import annotations

from typing import Any

from shared.money import naira_to_json, require_naira, to_naira

FUNDING_ADJUSTMENT_REVIEW_STATE = "funding_adjustment"


def _value(payload: Any, key: str) -> Any:
    if isinstance(payload, dict):
        return payload.get(key)
    return getattr(payload, key, None)


def build_funding_plan_signature(payload: Any) -> dict[str, Any]:
    explicit_split = _value(payload, "explicit_split") or {}
    normalized_split = {
        str(k): naira_to_json(v)
        for k, v in sorted(explicit_split.items(), key=lambda item: str(item[0]))
        if str(k).strip() and naira_to_json(v) is not None
    }
    source_accounts = sorted([str(bank) for bank in (_value(payload, "source_accounts") or []) if str(bank).strip()])
    return {
        "planned_for_amount": naira_to_json(_value(payload, "amount")) or "0.00",
        "planned_for_source_account_id": _value(payload, "source_account_id"),
        "planned_for_source_accounts": source_accounts,
        "planned_for_use_dual_accounts": bool(_value(payload, "use_dual_accounts")),
        "planned_for_explicit_split": normalized_split,
    }


def funding_plan_matches_signature(plan: dict[str, Any], signature: dict[str, Any]) -> bool:
    return all(plan.get(key) == expected for key, expected in signature.items())


def funding_plan_is_sufficient(plan: dict[str, Any], payload: Any) -> bool:
    if not plan.get("is_sufficient"):
        return False

    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return False

    total_funded = to_naira(plan.get("total_funded"))
    amount = to_naira(_value(payload, "amount"))
    if total_funded is None or amount is None:
        return False
    return total_funded >= require_naira(amount)


def funding_plan_confirmability(payload: Any) -> tuple[bool, str | None]:
    plan = _value(payload, "funding_plan")
    if not isinstance(plan, dict) or not plan:
        return False, "missing"
    if not funding_plan_is_sufficient(plan, payload):
        return False, "insufficient"
    if not funding_plan_matches_signature(plan, build_funding_plan_signature(payload)):
        return False, "stale"
    return True, None


def funding_adjustment_details(reason: str | None) -> dict[str, Any]:
    return {
        "review_state": FUNDING_ADJUSTMENT_REVIEW_STATE,
        "funding_plan_status": reason or "unknown",
    }


__all__ = [
    "FUNDING_ADJUSTMENT_REVIEW_STATE",
    "build_funding_plan_signature",
    "funding_adjustment_details",
    "funding_plan_confirmability",
    "funding_plan_is_sufficient",
    "funding_plan_matches_signature",
]
