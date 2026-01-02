"""Shared models for fault-tolerant conversation state.

Provides:
- ConfidenceLevel enum (HIGH/MEDIUM/LOW)
- ConversationAnchor for maintaining context across turns
- GraphStateSnapshot for per-graph state tracking
- FaultTolerantState for conversation-wide fault tolerance
"""

from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ConfidenceLevel(str, Enum):
    """Classification confidence levels for behavior branching."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @classmethod
    def from_score(cls, score: float) -> "ConfidenceLevel":
        """Convert a 0-1 confidence score to a level."""
        if score >= 0.8:
            return cls.HIGH
        elif score >= 0.5:
            return cls.MEDIUM
        return cls.LOW


class GraphType(str, Enum):
    """Types of conversation graphs."""

    QUERY = "query"
    TRANSFER = "transfer"
    AIRTIME = "airtime"
    DATA = "data"
    SUPPORT = "support"


GRAPH_FRESHNESS_WINDOWS = {
    GraphType.QUERY: 24 * 60 * 60,  # 24 hours
    GraphType.TRANSFER: 10 * 60,  # 10 minutes
    GraphType.AIRTIME: 10 * 60,  # 10 minutes
    GraphType.DATA: 10 * 60,  # 10 minutes
    GraphType.SUPPORT: None,  # Until resolved (no expiry)
}


class ConversationAnchor(BaseModel):
    """Anchor for maintaining context across turns.

    Examples:
    - query_handle_id for analytics/query results
    - transaction_id for transfers
    - support_ticket_id for support requests
    """

    anchor_type: str = Field(description="Type of anchor (query, transfer, support_ticket, etc.)")
    anchor_id: str = Field(description="Unique identifier for this anchor")
    graph_type: GraphType = Field(description="Which graph owns this anchor")
    created_at: datetime = Field(default_factory=datetime.now)
    state_snapshot: dict[str, Any] = Field(default_factory=dict, description="Snapshot of relevant state")

    @property
    def expires_at(self) -> datetime | None:
        """Calculate expiry based on graph type freshness window."""
        window = GRAPH_FRESHNESS_WINDOWS.get(self.graph_type)
        if window is None:
            return None  # Never expires
        return self.created_at + timedelta(seconds=window)

    @property
    def is_expired(self) -> bool:
        """Check if this anchor has expired."""
        expires = self.expires_at
        if expires is None:
            return False
        return datetime.now() > expires


class GraphStateSnapshot(BaseModel):
    """Per-graph state snapshot for recovery and replay.

    Each graph maintains its own last successful state.
    """

    graph_type: GraphType
    last_success_time: datetime = Field(default_factory=datetime.now)
    last_successful_state: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_fresh(self) -> bool:
        """Check if this snapshot is still within freshness window."""
        window = GRAPH_FRESHNESS_WINDOWS.get(self.graph_type)
        if window is None:
            return True
        return (datetime.now() - self.last_success_time).total_seconds() < window


class FaultTolerantState(BaseModel):
    """Shared state for fault-tolerant conversations.

    This is attached to the conversation context and provides:
    - Confidence tracking
    - Clarification attempt counting
    - Per-graph state snapshots
    - Global "last active" tracking
    """

    confidence_level: ConfidenceLevel = ConfidenceLevel.HIGH

    clarification_attempts: int = 0
    max_clarification_attempts: int = 2

    # Per-graph state snapshots
    graph_snapshots: dict[str, GraphStateSnapshot] = Field(default_factory=dict)

    # Global fallback (for implicit commands like "show", "next")
    last_active_graph: GraphType | None = None
    last_active_time: datetime | None = None

    current_anchor: ConversationAnchor | None = None

    def record_success(self, graph_type: GraphType, state: dict[str, Any]) -> None:
        """Record a successful interaction for a graph."""
        self.graph_snapshots[graph_type.value] = GraphStateSnapshot(
            graph_type=graph_type,
            last_success_time=datetime.now(),
            last_successful_state=state,
        )
        self.last_active_graph = graph_type
        self.last_active_time = datetime.now()
        self.clarification_attempts = 0

    def get_snapshot(self, graph_type: GraphType) -> GraphStateSnapshot | None:
        """Get the last successful state for a graph."""
        snapshot = self.graph_snapshots.get(graph_type.value)
        if snapshot and snapshot.is_fresh:
            return snapshot
        return None

    def increment_clarification(self) -> bool:
        """Increment clarification attempts. Returns True if limit reached."""
        self.clarification_attempts += 1
        return self.clarification_attempts >= self.max_clarification_attempts

    def reset_clarification(self) -> None:
        """Reset clarification counter (on successful understanding)."""
        self.clarification_attempts = 0

    def should_offer_recovery(self) -> bool:
        """Check if we should offer recovery options (3+ attempts)."""
        return self.clarification_attempts >= self.max_clarification_attempts

    def get_recovery_message(self) -> str:
        """Get the recovery message for total failure scenario."""
        return (
            "I'm having trouble understanding, and I don't want to waste your time.\n\n"
            "You can:\n"
            "• Rephrase what you want to do\n"
            "• Start a new request\n"
            "• Talk to support"
        )
