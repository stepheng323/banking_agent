"""Bounded support diagnostic agent.

The agent only chooses a safe support route from compact, pre-collected
read-only context. The worker remains responsible for policy, tickets, retry
handoffs, receipts, and all transaction execution.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from apps.chat.src.agent.workers.support.models import (
    ClassificationResult,
    SupportContext,
    SupportDiagnosticDecision,
    SupportIntent,
    SupportReferenceCandidate,
)

_MAX_RECENT_CANDIDATES = 5


SUPPORT_DIAGNOSTIC_SYSTEM_PROMPT = """You are a bounded diagnostic router for banking support.

Choose exactly one next_action from the schema.

Safety boundaries:
- You do not move money.
- You do not call payment, transfer, airtime, or data providers.
- You do not create tickets directly.
- You only classify the safest next support route from the supplied context.
- Deterministic code will enforce capability policy and perform any ticket, receipt, or retry handoff.

Routing guidance:
- Failed, pending, debit/refund, status, receipt, and retry requests need a transaction reference.
- Ticket status requests need a ticket reference or latest ticket context.
- Retry requests prepare a retry handoff only; they never execute a retry.
- Fraud and human-handoff requests should create or escalate a ticket.
- If the supplied context is not enough, ask for a reference or clarification.
- If unsure, use fallback_micro_resolver.
"""


def _compact_transaction(transaction: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(transaction, dict):
        return None
    allowed = (
        "id",
        "transaction_id",
        "transaction_type",
        "status",
        "amount",
        "currency",
        "recipient_name",
        "recipient_account_number",
        "recipient_bank_name",
        "source_bank_name",
        "error_message",
        "failure_category",
        "provider_status",
        "provider_error_code",
        "created_at",
        "completed_at",
        "unified_source",
        "local_transaction_id",
        "bank_transaction_id",
        "provider_reference",
        "local_status",
        "bank_status",
        "display_status",
        "match_confidence",
        "needs_review",
        "actionable",
    )
    return {key: transaction.get(key) for key in allowed if transaction.get(key) not in (None, "")}


def _compact_candidate(candidate: SupportReferenceCandidate) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "transaction_id": candidate.transaction_id,
            "ordinal": candidate.ordinal,
            "task_type": candidate.task_type,
            "amount": candidate.amount,
            "recipient_name": candidate.recipient_name,
            "recipient_resolved_name": candidate.recipient_resolved_name,
            "recipient_label": candidate.recipient_label,
            "bank_display": candidate.bank_display,
            "account_display": candidate.account_display,
            "final_status": candidate.final_status,
            "error_message": candidate.error_message,
            "failure_category": candidate.failure_category,
            "receipt_allowed": candidate.receipt_allowed,
        }.items()
        if value not in (None, "")
    }


def _compact_candidates(candidates: list[SupportReferenceCandidate]) -> list[dict[str, Any]]:
    return [_compact_candidate(candidate) for candidate in candidates[:_MAX_RECENT_CANDIDATES]]


def _compact_support_context(support_context: SupportContext) -> dict[str, Any]:
    context: dict[str, Any] = {
        "last_transaction_ref": support_context.last_transaction_ref,
        "last_ticket_id": support_context.last_ticket_id,
        "last_issue_intent": support_context.last_issue_intent.value if support_context.last_issue_intent else None,
        "last_support_step": support_context.last_support_step,
        "attempts": support_context.attempts,
    }

    if support_context.pending_reference is not None:
        context["pending_reference"] = {
            "source": support_context.pending_reference.source,
            "intent": support_context.pending_reference.intent,
            "candidates": _compact_candidates(support_context.pending_reference.candidates),
        }

    if support_context.receipt_thread_state is not None:
        thread = support_context.receipt_thread_state
        context["receipt_thread_state"] = {
            "async_group_id": thread.async_group_id,
            "candidates": _compact_candidates(thread.candidates),
            "served_transaction_ids": thread.served_transaction_ids[:_MAX_RECENT_CANDIDATES],
            "remaining_transaction_ids": thread.remaining_transaction_ids[:_MAX_RECENT_CANDIDATES],
            "last_selector_result_ids": thread.last_selector_result_ids[:_MAX_RECENT_CANDIDATES],
            "last_served_transaction_ids": thread.last_served_transaction_ids[:_MAX_RECENT_CANDIDATES],
        }

    return {key: value for key, value in context.items() if value not in (None, "", [])}


def build_support_diagnostic_context(
    *,
    message: str,
    locale: str,
    support_context: SupportContext,
    intent: SupportIntent,
    classification: ClassificationResult | None,
    transaction: dict[str, Any] | None,
    recent_candidates: list[SupportReferenceCandidate],
    ticket_code: str | None,
    latest_ticket_id: str | None,
    capability_summary: dict[str, bool],
) -> dict[str, Any]:
    """Build bounded read-only diagnostic context."""
    return {
        "message": message,
        "locale": locale,
        "support_context": _compact_support_context(support_context),
        "classification": {
            "intent": intent.value,
            "confidence": classification.confidence if classification else 1.0,
            "transaction_ref": (
                classification.transaction_ref.model_dump(mode="json")
                if classification and classification.transaction_ref
                else None
            ),
        },
        "resolved_transaction": _compact_transaction(transaction),
        "recent_candidates": _compact_candidates(recent_candidates),
        "ticket": {
            "explicit_code": ticket_code,
            "latest_ticket_id": latest_ticket_id,
        },
        "capability_policy": capability_summary,
    }


class SupportDiagnosticAgent:
    """Single-call structured diagnostic router for support."""

    def __init__(self, llm: Any) -> None:
        self.structured = llm.with_structured_output(SupportDiagnosticDecision)

    async def decide(self, diagnostic_context: dict[str, Any]) -> SupportDiagnosticDecision:
        messages = [
            SystemMessage(content=SUPPORT_DIAGNOSTIC_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    "Return only the structured support diagnostic decision for this context:\n"
                    f"{json.dumps(diagnostic_context, ensure_ascii=True, sort_keys=True)}"
                )
            ),
        ]
        raw = await self.structured.ainvoke(messages)
        if isinstance(raw, SupportDiagnosticDecision):
            return raw
        return SupportDiagnosticDecision.model_validate(raw)
