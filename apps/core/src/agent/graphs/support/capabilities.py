"""Support graph capability definitions.

Action-based capabilities for micro-resolver.
Defines what actions Support can take, not just what it can show.
"""

from enum import Enum


class SupportIntent(str, Enum):
    """Classified support intent types."""
    
    FAILED_TRANSFER = "failed_transfer"
    PENDING_TRANSFER = "pending_transfer"
    REVERSAL_REFUND = "reversal_refund"
    WRONG_RECIPIENT = "wrong_recipient"
    FRAUD_REPORT = "fraud_report"
    ACCOUNT_LINKING = "account_linking"
    LIMITS_FEES = "limits_fees"
    RECEIPT_REQUEST = "receipt_request"
    HUMAN_HANDOFF = "human_handoff"
    GENERAL_TX_ISSUE = "general_tx_issue"  # "issue with my transaction"


class SupportAction(str, Enum):
    """Actions Support can take (capabilities)."""
    
    LOOKUP_TRANSACTION = "lookup_transaction"
    EXPLAIN_STATUS = "explain_status"
    RETRY_PAYOUT = "retry_payout"
    INITIATE_REFUND = "initiate_refund"
    QUEUE_REFUND_REQUEST = "queue_refund_request"
    COLLECT_DETAILS = "collect_details"
    CREATE_TICKET = "create_ticket"
    ESCALATE = "escalate"


# What actions are actually available (implemented)
SUPPORTED_ACTIONS: list[SupportAction] = [
    SupportAction.LOOKUP_TRANSACTION,
    SupportAction.EXPLAIN_STATUS,
    SupportAction.COLLECT_DETAILS,
    SupportAction.ESCALATE,  # Can still escalate to human
]


# Actions that require implementation or aren't automated yet
UNAVAILABLE_ACTIONS: list[SupportAction] = [
    SupportAction.RETRY_PAYOUT,
    SupportAction.INITIATE_REFUND,
    SupportAction.QUEUE_REFUND_REQUEST,
    SupportAction.CREATE_TICKET,  # TODO: integrate with ticketing system
]


# Limits for resolver
SUPPORT_LIMITS = {
    "max_escalation_attempts": 3,
    "max_tx_lookback_days": 90,
    "sla_pending_hours": 24,  # After which to auto-escalate
}


ACTION_LABELS: dict[SupportAction, str] = {
    SupportAction.LOOKUP_TRANSACTION: "look up transaction",
    SupportAction.EXPLAIN_STATUS: "explain what happened",
    SupportAction.RETRY_PAYOUT: "retry the transfer",
    SupportAction.INITIATE_REFUND: "process refund immediately",
    SupportAction.QUEUE_REFUND_REQUEST: "submit refund request",
    SupportAction.COLLECT_DETAILS: "collect more details",
    SupportAction.CREATE_TICKET: "create support ticket",
    SupportAction.ESCALATE: "escalate to human support",
}


# What to offer when action isn't available
ACTION_ALTERNATIVES: dict[SupportAction, SupportAction] = {
    SupportAction.RETRY_PAYOUT: SupportAction.ESCALATE,
    SupportAction.INITIATE_REFUND: SupportAction.ESCALATE,
    SupportAction.QUEUE_REFUND_REQUEST: SupportAction.ESCALATE,
    SupportAction.CREATE_TICKET: SupportAction.ESCALATE,
}


def check_actions(requested: list[SupportAction]) -> list[SupportAction]:
    """Check which requested actions are not supported."""
    return [action for action in requested if action not in SUPPORTED_ACTIONS]


def get_alternative(action: SupportAction) -> SupportAction | None:
    """Get alternative action if requested one isn't available."""
    return ACTION_ALTERNATIVES.get(action)


def generate_limitation_message(missing: list[SupportAction]) -> str:
    """Generate negotiation message for unavailable actions."""
    if not missing:
        return ""

    action = missing[0]
    alt = get_alternative(action)
    label = ACTION_LABELS.get(action, action.value)
    
    if action == SupportAction.RETRY_PAYOUT:
        return (
            f"I can't *{label}* automatically right now.\n\n"
            "I can create a support ticket for the team to retry it. Want me to do that?"
        )

    if action == SupportAction.INITIATE_REFUND:
        return (
            f"I can't *{label}* instantly, but I can submit a refund request.\n\n"
            "The team will process it within 24-48 hours. Want me to submit it?"
        )

    if alt:
        alt_label = ACTION_LABELS.get(alt, alt.value)
        return f"I can't *{label}* yet, but I can *{alt_label}*. Want me to proceed?"
    
    return f"*{label.title()}* isn't available yet. I'll escalate this to support."
