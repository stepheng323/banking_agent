"""Models for the FAQ worker."""

from pydantic import BaseModel, Field


class FAQRetrievalHit(BaseModel):
    """A single FAQ retrieval result."""

    id: str
    category: str
    question: str
    answer: str
    score: float
    match_type: str = "keyword"  # "keyword", "fuzzy", "semantic"


class FAQQueryResult(BaseModel):
    """Result of FAQ query processing."""

    query: str
    normalized_query: str
    keywords: list[str]
    hits: list[FAQRetrievalHit] = Field(default_factory=list)
    confidence: float = 0.0
    top_category: str | None = None


class ForbiddenScopeResult(BaseModel):
    """Result of forbidden scope detection."""

    is_forbidden: bool
    reason: str | None = None
    detected_patterns: list[str] = Field(default_factory=list)
