"""Subgraph Contract Types.

Defines the structured input and discriminated result types for subgraph workers.
"""

from enum import Enum
from typing import Literal


class SubgraphResultStatus(str, Enum):
    """Discriminated result status for subgraph returns."""

    OK = "ok"
    NEEDS_INPUT = "needs_input"
    NEEDS_CONFIRMATION = "needs_confirmation"
    NEEDS_AUTH = "needs_auth"
    CONFLICT = "conflict"
    FAILED = "failed"


SubgraphMode = Literal["resolve", "validate", "extract", "execute"]
