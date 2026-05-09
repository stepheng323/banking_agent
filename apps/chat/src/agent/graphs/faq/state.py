"""State definition for FAQ graph."""

from typing import TypedDict


class FAQState(TypedDict, total=False):
    """State for FAQ flow graph."""

    # Input
    phone_number: str
    language: str
    message: str
    message_id: str

    # Processing
    normalized_query: str
    extracted_keywords: list[str]
    detected_category: str | None

    # Retrieval
    retrieved_entries: list[dict]  # [{question, answer, score, category}]
    retrieval_confidence: float  # 0-1 confidence score

    # Validation
    is_forbidden_scope: bool
    forbidden_reason: str | None

    # Output
    response: str
    should_route_to_support: bool
    error: str | None
