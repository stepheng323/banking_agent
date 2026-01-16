"""Support graph state definition."""

from typing import Any, TypedDict

from apps.core.src.agent.graphs.support.models import (
    ClassificationResult,
    EscalationResult,
    SupportContext,
    SupportExtractionResult,
    SupportIntent,
    SupportResponse,
)
from apps.core.src.agent.graphs.support.micro_resolver import (
    Decision,
    NextStep,
    ResolverDecision,
)


class SupportGraphState(TypedDict, total=False):
    """State for support flow graph."""

    # Input
    phone_number: str
    message: str
    message_id: str
    user_id: str
    quoted_message_id: str | None

    # Classification (from LLM)
    intent: SupportIntent | None
    classification: ClassificationResult | None
    extraction: SupportExtractionResult | None  # v2: structured extraction

    # Micro-resolver decision
    resolver_decision: ResolverDecision | None
    decision: Decision | None
    next_step: NextStep | None

    # Session context (persisted to Redis)
    support_context: SupportContext | None

    # Transaction context
    transaction_id: str | None
    transaction: dict[str, Any] | None
    resolution_method: str | None

    # Ticket context
    ticket_id: str | None
    ticket_code: str | None

    # Response
    response: SupportResponse | None
    escalation: EscalationResult | None
    final_message: str

    # Flow control
    error: str | None
    needs_clarification: bool
    clarification_question: str | None
    notify_human: bool
