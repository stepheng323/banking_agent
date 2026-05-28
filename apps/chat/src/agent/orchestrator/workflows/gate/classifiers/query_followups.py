from typing import Any

from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.transaction_intents import (
    _classify_obvious_transfer_request,
    _is_obvious_airtime_request,
    _is_obvious_data_request,
)
from apps.chat.src.agent.workers.query.services.parsing.parser import QueryParser
from apps.chat.src.agent.workers.query.services.reasoning.shortcuts import resolve_query_shortcut_with_reason
from apps.chat.src.agent.workers.query.utils.timezone import lagos_today


def _query_followup_bypass_reason(
    *,
    message_text: str,
    locale: str,
    query_session_snapshot: dict[str, Any] | None,
    has_context_frames: bool = False,
) -> tuple[str | None, str | None]:
    if not isinstance(query_session_snapshot, dict) or not query_session_snapshot.get("session_active"):
        return None, None

    transfer_request_reason = _classify_obvious_transfer_request(message_text)
    if transfer_request_reason or _is_obvious_airtime_request(message_text) or _is_obvious_data_request(message_text):
        return None, "fresh_transaction_request"

    shortcut, miss_reason = resolve_query_shortcut_with_reason(message_text, locale)
    if shortcut is not None:
        return "query_shortcut", shortcut.action

    if query_session_snapshot.get("pending_clarification"):
        parsed_time_range = QueryParser.parse_clarification_time_range(message_text, today=lagos_today())
        if parsed_time_range is not None:
            return "pending_clarification", parsed_time_range.period or "days_back"
        return None, miss_reason

    if has_context_frames:
        return "active_query_session", miss_reason or "query_session_active"
    return None, miss_reason
