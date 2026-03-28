"""Query continuity facade."""

from apps.core.src.agent.graphs.query.continuations.classifier import ContinuationClassifier
from apps.core.src.agent.graphs.query.continuations.messaging import (
    build_soft_clarification,
    get_recovery_message,
    should_offer_recovery,
)
from apps.core.src.agent.graphs.query.continuations.transforms import rebuild_query_contract

__all__ = [
    "ContinuationClassifier",
    "build_soft_clarification",
    "get_recovery_message",
    "rebuild_query_contract",
    "should_offer_recovery",
]
