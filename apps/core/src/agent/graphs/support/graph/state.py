"""Support graph state definition."""

from typing import Any, TypedDict

from apps.core.src.agent.graphs.support.micro_resolver import (
    Decision,
    NextStep,
    ResolverDecision,
)
from apps.core.src.agent.graphs.support.models import (
    ClassificationResult,
    EscalationResult,
    SupportContext,
    SupportExtractionResult,
    SupportIntent,
    SupportResponse,
)


class SupportGraphState(TypedDict, total=False):
    """State for support flow graph."""

    phone_number: str
    message: str
    message_id: str
    user_id: str
    quoted_message_id: str | None

    intent: SupportIntent | None
    classification: ClassificationResult | None
    extraction: SupportExtractionResult | None
    resolver_decision: ResolverDecision | None
    decision: Decision | None
    next_step: NextStep | None
    support_context: SupportContext | None
    transaction_id: str | None
    transaction: dict[str, Any] | None
    resolution_method: str | None
    ticket_id: str | None
    ticket_code: str | None
    response: SupportResponse | None
    escalation: EscalationResult | None
    final_message: str
    error: str | None
    needs_clarification: bool
    clarification_question: str | None
    notify_human: bool
