from apps.chat.src.agent.orchestrator.guardrails.cancellation import is_obvious_cancel_message
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.direct_domains import (
    _is_account_balance_request,
    _is_account_domain_request,
    _is_beneficiary_domain_request,
    _is_query_domain_request,
)
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
    has_recent_query_context: bool = False,
    is_pending_clarification: bool = False,
) -> tuple[str | None, str | None]:
    if not has_active_query_session and not has_recent_query_context:
        return None, None

    # Do not let a live query claim an explicit request that has its own
    # domain owner (or a request to end the active session).
    if is_obvious_cancel_message(message_text):
        return None, "explicit_cancel"
    if (
        _is_account_balance_request(message_text)
        or _is_account_domain_request(message_text)
        or _is_beneficiary_domain_request(message_text)
    ):
        return None, "explicit_non_query_domain"

    transfer_request_reason = _classify_obvious_transfer_request(message_text)
    if transfer_request_reason or _is_obvious_airtime_request(message_text) or _is_obvious_data_request(message_text):
        return None, "fresh_transaction_request"

    # A complete query request is a replacement, not a continuation.  The
    # query worker receives it directly and starts a fresh contract.
    if has_active_query_session and _is_query_domain_request(message_text):
        return "replacement_query", "explicit_query_command"

    shortcut, miss_reason = resolve_query_shortcut_with_reason(message_text, locale)
    if shortcut is not None:
        return "query_shortcut", shortcut.action

    if is_pending_clarification:
        return "pending_clarification", miss_reason or "query_session_active"

    if has_active_query_session:
        return "active_query_session", miss_reason or "query_session_active"
    normalized = " ".join(message_text.casefold().split())
    if has_recent_query_context and any(
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
