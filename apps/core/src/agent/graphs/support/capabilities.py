"""Support graph capability definitions."""

from enum import Enum

from shared.i18n import render_capability_limitation
from shared.policy.adapters import resolve_capability_alternative, resolve_capability_rule


class SupportAction(str, Enum):
    """Actions Support can take."""

    LOOKUP_TRANSACTION = "lookup_transaction"
    EXPLAIN_STATUS = "explain_status"
    RETRY_PAYOUT = "retry_payout"
    INITIATE_REFUND = "initiate_refund"
    QUEUE_REFUND_REQUEST = "queue_refund_request"
    COLLECT_DETAILS = "collect_details"
    CREATE_TICKET = "create_ticket"
    ESCALATE = "escalate"


SUPPORT_LIMITS = {
    "max_escalation_attempts": 3,
    "max_tx_lookback_days": 90,
    "sla_pending_hours": 24,
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


def check_actions(requested: list[SupportAction]) -> list[SupportAction]:
    """Check which requested actions are not supported.

    Policy is authoritative: if an action has no rule, treat it as unsupported.
    """
    missing: list[SupportAction] = []
    for action in requested:
        policy_rule = resolve_capability_rule(domain="support", action=action.value)
        if policy_rule is None or not policy_rule.supported:
            missing.append(action)
    return missing


def get_alternative(action: SupportAction) -> SupportAction | None:
    """Get alternative action if requested one isn't available."""
    policy_alternative = resolve_capability_alternative(domain="support", action=action.value)
    if policy_alternative:
        try:
            return SupportAction(policy_alternative)
        except ValueError:
            return None
    return None


def generate_limitation_message(missing: list[SupportAction], *, locale: str = "en") -> str:
    """Generate negotiation message for unavailable actions."""
    if not missing:
        return ""

    action = missing[0]

    alt = get_alternative(action)
    label = ACTION_LABELS.get(action, action.value)

    alt_label = ACTION_LABELS.get(alt, alt.value) if alt else None
    return render_capability_limitation(
        locale=locale,
        action_label=label,
        alternative_labels=[alt_label] if alt_label else [],
    )
