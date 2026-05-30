"""Support worker capability definitions."""

from enum import Enum

from banking.policy.adapters import check_unsupported_actions, resolve_capability_alternative
from banking.policy.guardrails.loader import get_cached_guardrails
from banking.policy.service import capability_block_message
from banking.presentation.i18n.bridge import render_capability_limitation


class SupportAction(str, Enum):
    """Actions Support can take."""

    LOOKUP_TRANSACTION = "lookup_transaction"
    LOOKUP_TICKET = "lookup_ticket"
    EXPLAIN_STATUS = "explain_status"
    RETRY_PAYOUT = "retry_payout"
    INITIATE_REFUND = "initiate_refund"
    QUEUE_REFUND_REQUEST = "queue_refund_request"
    COLLECT_DETAILS = "collect_details"
    CREATE_TICKET = "create_ticket"
    ESCALATE = "escalate"


SUPPORT_LIMITS = get_cached_guardrails().support.model_dump()

ACTION_LABELS: dict[SupportAction, str] = {
    SupportAction.LOOKUP_TRANSACTION: "look up transaction",
    SupportAction.LOOKUP_TICKET: "look up support ticket",
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
    missing = check_unsupported_actions(domain="support", requested_actions=[action.value for action in requested])
    return [action for action in requested if action.value in missing]


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

    explicit = capability_block_message(domain="support", action=action.value, locale=locale)
    if explicit:
        return explicit

    alt = get_alternative(action)
    label = ACTION_LABELS.get(action, action.value)

    alt_label = ACTION_LABELS.get(alt, alt.value) if alt else None
    return render_capability_limitation(
        locale=locale,
        action_label=label,
        alternative_labels=[alt_label] if alt_label else [],
    )
