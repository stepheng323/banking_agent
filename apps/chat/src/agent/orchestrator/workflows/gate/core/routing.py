"""Routing constants and semantic route accessors for the gate workflow."""

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
