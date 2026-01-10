"""Support graph state definition."""

from typing import Any, TypedDict

from apps.core.src.agent.graphs.support.models import (
    ClassificationResult,
    EscalationResult,
    SupportIntent,
    SupportResponse,
)


class SupportGraphState(TypedDict, total=False):
    """State for support flow graph."""

    # Input
    phone_number: str
    message: str
    message_id: str
    user_id: str
    quoted_message_id: str | None

    # Classification
    intent: SupportIntent | None
    classification: ClassificationResult | None

    # Transaction context
    transaction_id: str | None
    transaction: dict[str, Any] | None  # Hydrated transaction data
    resolution_method: str | None  # "quoted", "explicit", "recent", "ambiguous"

    # Response
    response: SupportResponse | None
    escalation: EscalationResult | None
    final_message: str

    # Flow control
    error: str | None
    needs_clarification: bool
    clarification_question: str | None
