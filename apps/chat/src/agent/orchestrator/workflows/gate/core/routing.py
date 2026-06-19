"""Routing constants and observability fields for the gate workflow."""

from typing import Any

TRANSACTION_EXECUTORS = {"transfer", "airtime", "data"}
DIRECT_DOMAIN_ACTIONS = {
    "transfer": "send_money",
    "airtime": "buy_airtime",
    "data": "buy_data",
    "support": "collect_details",
    "faq": "answer_question",
}


def _semantic_route_decision(route: Any) -> str | None:
    decision = str(getattr(route, "decision", "") or "")
    return decision or None


def _semantic_route_mode(route: Any) -> str | None:
    mode = getattr(route, "mode", None)
    if isinstance(mode, str) and mode:
        return mode
    return None


def _route_observability_updates(
    *,
    owner: str,
    decision: str,
    target_domain: str | None = None,
    mode: str | None = None,
    route_source: str | None = None,
    heuristic_type: str | None = None,
    heuristic_name: str | None = None,
) -> dict[str, Any]:
    return {
        "routing_owner": owner,
        "routing_decision": decision,
        "routing_target_domain": target_domain,
        "routing_mode": mode,
        "route_source": route_source or owner,
        "routing_heuristic_type": heuristic_type,
        "routing_heuristic_name": heuristic_name,
    }
