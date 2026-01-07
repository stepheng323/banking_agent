"""Final guard node - validate output for safety."""

import re

from apps.core.src.agent.sub_agents.faq.state import FAQState
from shared.utils.logging import get_logger

logger = get_logger(__name__)

# Patterns that indicate potential hallucination or unsafe output
UNSAFE_PATTERNS = [
    r"\byour transaction\b",  # Shouldn't reference specific transactions
    r"\byour balance is\b",  # Shouldn't state specific balances
    r"\b\d{10}\b",  # Account numbers
    r"₦\d+\.\d{2}\b",  # Specific amounts
    r"\bI can see that\b",  # Implies access to user data
    r"\baccording to your\b",  # Implies access to user data
    r"\blooking at your\b",  # Implies access to user data
]

# Patterns that indicate operational promises we shouldn't make
PROMISE_PATTERNS = [
    r"\bI will\s+(process|complete|fix)\b",
    r"\bI have\s+(processed|completed|fixed)\b",
    r"\byour (transfer|payment) (is|has been) (completed|processed)\b",
]


def final_guard_node(state: FAQState) -> FAQState:
    """
    Final validation of the response.

    Checks for:
    - Potential hallucinations (user-specific claims)
    - Operational promises that shouldn't be made
    - Unsafe patterns
    """
    response = state.get("response", "")

    if not response:
        return state

    issues = []

    # Check for unsafe patterns
    for pattern in UNSAFE_PATTERNS:
        if re.search(pattern, response, re.IGNORECASE):
            issues.append(f"Unsafe pattern: {pattern}")

    # Check for promise patterns
    for pattern in PROMISE_PATTERNS:
        if re.search(pattern, response, re.IGNORECASE):
            issues.append(f"Promise pattern: {pattern}")

    if issues:
        logger.warning(f"Guard detected issues in response: {issues}")
        # For now, log but don't modify. In production, could sanitize or regenerate.
        # This is a safety net, not expected to trigger often with good prompts.

    return state
