"""Support graph capability definitions.

Defines what the support graph can handle automatically vs needs escalation.
"""

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.support.models import ClassificationResult


class SupportCapability(str, Enum):
    """Capabilities for support handling."""

    TRANSFER_STATUS = "transfer_status"
    FAILURE_REASON = "failure_reason"
    PENDING_STATUS = "pending_status"
    REVERSAL_STATUS = "reversal_status"
    RETRY_TRANSFER = "retry_transfer"
    RECEIPT_REQUEST = "receipt_request"
    FRAUD_REPORT = "fraud_report"
    LIVE_CHAT = "live_chat"
    CALL_SUPPORT = "call_support"
    REFUND_REQUEST = "refund_request"


SUPPORT_SUPPORTS: list[SupportCapability] = [
    SupportCapability.TRANSFER_STATUS,
    SupportCapability.FAILURE_REASON,
    SupportCapability.PENDING_STATUS,
    SupportCapability.REVERSAL_STATUS,
    SupportCapability.RETRY_TRANSFER,
    SupportCapability.RECEIPT_REQUEST,
    SupportCapability.FRAUD_REPORT,
]


CAPABILITY_LABELS: dict[SupportCapability, str] = {
    SupportCapability.TRANSFER_STATUS: "transfer status check",
    SupportCapability.FAILURE_REASON: "failure investigation",
    SupportCapability.PENDING_STATUS: "pending transfer check",
    SupportCapability.REVERSAL_STATUS: "reversal/refund status",
    SupportCapability.RETRY_TRANSFER: "retry transfer",
    SupportCapability.RECEIPT_REQUEST: "receipt request",
    SupportCapability.FRAUD_REPORT: "fraud report",
    SupportCapability.LIVE_CHAT: "live chat with agent",
    SupportCapability.CALL_SUPPORT: "phone support",
    SupportCapability.REFUND_REQUEST: "refund request",
}


CAPABILITY_ALTERNATIVES: dict[SupportCapability, list[SupportCapability]] = {
    SupportCapability.LIVE_CHAT: [SupportCapability.FRAUD_REPORT],
    SupportCapability.CALL_SUPPORT: [SupportCapability.FRAUD_REPORT],
    SupportCapability.REFUND_REQUEST: [SupportCapability.REVERSAL_STATUS],
}


def check_capabilities(requires: list[SupportCapability]) -> list[SupportCapability]:
    """Check which required capabilities are missing."""
    return [cap for cap in requires if cap not in SUPPORT_SUPPORTS]


def derive_requirements(
    user_message: str,
) -> list[SupportCapability]:
    """Derive required capabilities from user message."""
    requires: list[SupportCapability] = []
    msg_lower = user_message.lower()

    live_chat_keywords = ["live chat", "talk to someone", "speak to agent", "human agent", "real person"]
    if any(kw in msg_lower for kw in live_chat_keywords):
        requires.append(SupportCapability.LIVE_CHAT)

    call_keywords = ["call me", "phone call", "call support", "speak on phone"]
    if any(kw in msg_lower for kw in call_keywords):
        requires.append(SupportCapability.CALL_SUPPORT)

    refund_keywords = ["refund", "money back", "return my money", "give me back"]
    if any(kw in msg_lower for kw in refund_keywords):
        requires.append(SupportCapability.REFUND_REQUEST)

    return list(set(requires))


def generate_limitation_message(missing: list[SupportCapability]) -> str:
    """Generate user-friendly limitation message."""
    if not missing:
        return ""

    if SupportCapability.LIVE_CHAT in missing:
        return (
            "Live chat with a human agent isn't available in this channel yet.\n\n"
            "I can help you with:\n"
            "• Check transfer status\n"
            "• Investigate failed transfers\n"
            "• Report fraud/suspicious activity\n\n"
            "What would you like help with?"
        )

    if SupportCapability.CALL_SUPPORT in missing:
        return (
            "Phone support isn't available through this channel.\n\n"
            "I can help you resolve most issues here. What's the problem?"
        )

    if SupportCapability.REFUND_REQUEST in missing:
        return (
            "Refund requests need to go through your bank.\n\n"
            "I can check the reversal status if you give me the transfer details."
        )

    missing_labels = [CAPABILITY_LABELS.get(cap, cap.value) for cap in missing]
    return f"*{missing_labels[0].title()}* isn't available yet."
