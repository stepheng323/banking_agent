"""Subgraph Contract Types.

Defines the structured input and discriminated result types for subgraph workers.
"""

from enum import Enum
from typing import Literal

# --- Result Status (Discriminated Union Type) ---


class SubgraphResultStatus(str, Enum):
    """Discriminated result status for subgraph returns."""

    OK = "ok"  # Task can progress
    NEEDS_INPUT = "needs_input"  # Missing required fields
    NEEDS_CONFIRMATION = "needs_confirmation"  # Awaiting user confirmation
    NEEDS_AUTH = "needs_auth"  # Awaiting PIN verification
    CONFLICT = "conflict"  # Ambiguity or capability conflict
    FAILED = "failed"  # Execution failed


# --- Execution Mode ---

SubgraphMode = Literal["resolve", "validate", "extract", "execute"]
