from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.transaction_intents import (
    _classify_obvious_transfer_request,
    _is_obvious_airtime_request,
    _is_obvious_data_request,
)
from banking.transactions.query.services.reasoning.shortcuts import resolve_query_shortcut_with_reason


def _query_followup_bypass_reason(
    *,
    message_text: str,
    locale: str,
    has_active_query_session: bool,
    has_context_frames: bool = False,
    is_pending_clarification: bool = False,
) -> tuple[str | None, str | None]:
    if not has_active_query_session and not has_context_frames:
        return None, None

    transfer_request_reason = _classify_obvious_transfer_request(message_text)
    if transfer_request_reason or _is_obvious_airtime_request(message_text) or _is_obvious_data_request(message_text):
        return None, "fresh_transaction_request"

    shortcut, miss_reason = resolve_query_shortcut_with_reason(message_text, locale)
    if shortcut is not None:
        return "query_shortcut", shortcut.action

    if is_pending_clarification:
        return "pending_clarification", miss_reason or "query_session_active"

    if has_active_query_session:
        return "active_query_session", miss_reason or "query_session_active"
    normalized = " ".join(message_text.casefold().split())
    if has_context_frames and any(
        marker in normalized
        for marker in (
            "that",
            "those",
            "the first",
            "the second",
            "the third",
            "one",
            "previous",
            "earlier",
            "actually",
            "show them",
            "details",
            "reference",
            "receipt",
        )
    ):
        return "recent_query_context", miss_reason or "read_only_referential_followup"
    return None, miss_reason
