"""Planner runner route metadata helpers."""

from typing import Any

_PLANNER_DOMAIN_TARGETS = {"query", "account", "support", "beneficiary", "transfer", "airtime", "data", "schedule"}


def _planner_route_updates(
    *,
    decision: str,
    planner_output: Any | None = None,
    target_domain: str | None = None,
) -> dict[str, Any]:
    resolved_target = target_domain
    if resolved_target is None and planner_output is not None:
        primary_intent = str(getattr(planner_output, "primary_intent", "") or "").strip().lower()
        if primary_intent in _PLANNER_DOMAIN_TARGETS:
            resolved_target = primary_intent
    return {
        "routing_owner": "planner",
        "routing_decision": decision,
        "routing_target_domain": resolved_target,
        "planner_used": True,
    }


__all__ = ["_planner_route_updates"]
