"""Support micro-resolver.

Lightweight resolver for support flows:
- Checks requested actions against capabilities
- Determines next support step
- Tracks session context
- Decides when to escalate
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from apps.core.src.agent.graphs.support.capabilities import (
    SupportAction,
    SUPPORTED_ACTIONS,
    SUPPORT_LIMITS,
    check_actions,
    get_alternative,
    generate_limitation_message,
)
from apps.core.src.agent.graphs.support.models import (
    SupportExtractionResult,
    SupportContext,
    SupportIntent,
    RequestedAction,
    TransactionReference,
    EscalationResult,
)


class Decision(str, Enum):
    """Micro-resolver decision."""
    
    PROCEED = "PROCEED"           # Can handle with available actions
    NEGOTIATE = "NEGOTIATE"       # Requested action unavailable, offer alternative
    COLLECT = "COLLECT"           # Need more info (tx ref, details)
    ESCALATE = "ESCALATE"         # Hand off to human


class NextStep(str, Enum):
    """What to do next in the support flow."""
    
    ASK_REFERENCE = "ask_reference"
    LOOKUP_TRANSACTION = "lookup_transaction"
    EXPLAIN_STATUS = "explain_status"
    ASK_CLARIFICATION = "ask_clarification"
    CREATE_TICKET = "create_ticket"
    ESCALATE = "escalate"


class Prompt(BaseModel):
    """Templated prompt for response."""
    
    key: str
    vars: dict[str, Any] = Field(default_factory=dict)


class ResolverDecision(BaseModel):
    """Decision from support micro-resolver."""
    
    decision: Decision
    next_step: NextStep
    extraction: SupportExtractionResult
    context: SupportContext
    
    # For NEGOTIATE
    missing_actions: list[SupportAction] = Field(default_factory=list)
    negotiation_message: str | None = None
    
    # For ESCALATE
    escalation: EscalationResult | None = None
    
    prompts: list[Prompt] = Field(default_factory=list)


# Map LLM RequestedAction to resolver SupportAction
ACTION_MAP = {
    RequestedAction.LOOKUP_TRANSACTION: SupportAction.LOOKUP_TRANSACTION,
    RequestedAction.EXPLAIN_STATUS: SupportAction.EXPLAIN_STATUS,
    RequestedAction.RETRY_PAYOUT: SupportAction.RETRY_PAYOUT,
    RequestedAction.INITIATE_REFUND: SupportAction.INITIATE_REFUND,
    RequestedAction.CREATE_TICKET: SupportAction.CREATE_TICKET,
    RequestedAction.ESCALATE: SupportAction.ESCALATE,
}


def _has_transaction_ref(ref: TransactionReference) -> bool:
    """Check if we have enough to find the transaction."""
    return bool(
        ref.transaction_id or
        ref.use_quoted or
        ref.use_recent or
        (ref.amount and (ref.recipient_name or ref.date_hint))
    )


def _should_escalate(context: SupportContext, intent: SupportIntent) -> EscalationResult | None:
    """Determine if we should escalate to human support."""
    
    # Max attempts reached
    if context.attempts >= SUPPORT_LIMITS["max_escalation_attempts"]:
        return EscalationResult(
            reason="max_attempts",
            context={"attempts": context.attempts},
        )
    
    # Fraud always escalates
    if intent == SupportIntent.FRAUD_REPORT:
        return EscalationResult(
            reason="fraud_suspected",
            transaction_id=context.last_transaction_ref,
        )
    
    # User explicitly wants human
    if intent == SupportIntent.HUMAN_HANDOFF:
        return EscalationResult(
            reason="user_requested",
        )
    
    return None


def resolve(
    extraction: SupportExtractionResult,
    context: SupportContext | None = None,
    has_quoted_message: bool = False,
) -> ResolverDecision:
    """
    Main micro-resolver entry point.
    
    Flow:
    1. Check if should escalate immediately
    2. Check if transaction ref is available
    3. Check requested actions against capabilities
    4. Determine next step
    """
    # Initialize context if not provided
    if context is None:
        context = SupportContext()
    
    # Update context with this turn
    context.last_issue_intent = extraction.intent
    context.attempts += 1
    
    # Use quoted message if available
    if has_quoted_message and extraction.transaction_ref:
        extraction.transaction_ref.use_quoted = True
    
    # Check for immediate escalation
    escalation = _should_escalate(context, extraction.intent)
    if escalation:
        return ResolverDecision(
            decision=Decision.ESCALATE,
            next_step=NextStep.ESCALATE,
            extraction=extraction,
            context=context,
            escalation=escalation,
            prompts=[Prompt(key="support.escalating", vars={"reason": escalation.reason})],
        )
    
    # Check if we have transaction reference
    has_ref = _has_transaction_ref(extraction.transaction_ref)
    
    if not has_ref and extraction.intent not in (SupportIntent.LIMITS_FEES, SupportIntent.ACCOUNT_LINKING):
        # Need to collect transaction reference
        context.last_support_step = "asked_for_reference"
        return ResolverDecision(
            decision=Decision.COLLECT,
            next_step=NextStep.ASK_REFERENCE,
            extraction=extraction,
            context=context,
            prompts=[Prompt(
                key="support.ask_reference",
                vars={"intent": extraction.intent.value},
            )],
        )
    
    # Check requested actions
    requested = [ACTION_MAP[a] for a in extraction.requested_actions if a in ACTION_MAP]
    missing = check_actions(requested)
    
    if missing:
        alt = get_alternative(missing[0])
        msg = generate_limitation_message(missing)
        return ResolverDecision(
            decision=Decision.NEGOTIATE,
            next_step=NextStep.CREATE_TICKET if alt == SupportAction.CREATE_TICKET else NextStep.ASK_CLARIFICATION,
            extraction=extraction,
            context=context,
            missing_actions=missing,
            negotiation_message=msg,
            prompts=[Prompt(key="support.negotiate", vars={"message": msg})],
        )
    
    # Determine next step based on intent
    if extraction.intent in (SupportIntent.FAILED_TRANSFER, SupportIntent.PENDING_TRANSFER, SupportIntent.GENERAL_TX_ISSUE):
        context.last_support_step = "looking_up"
        return ResolverDecision(
            decision=Decision.PROCEED,
            next_step=NextStep.LOOKUP_TRANSACTION,
            extraction=extraction,
            context=context,
        )
    
    if extraction.intent == SupportIntent.RECEIPT_REQUEST:
        context.last_support_step = "looking_up"
        return ResolverDecision(
            decision=Decision.PROCEED,
            next_step=NextStep.LOOKUP_TRANSACTION,
            extraction=extraction,
            context=context,
        )
    
    if extraction.intent in (SupportIntent.REVERSAL_REFUND, SupportIntent.WRONG_RECIPIENT):
        context.last_support_step = "creating_ticket"
        return ResolverDecision(
            decision=Decision.PROCEED,
            next_step=NextStep.CREATE_TICKET,
            extraction=extraction,
            context=context,
            prompts=[Prompt(key="support.will_create_ticket", vars={})],
        )
    
    # Default: explain status
    context.last_support_step = "explaining"
    return ResolverDecision(
        decision=Decision.PROCEED,
        next_step=NextStep.EXPLAIN_STATUS,
        extraction=extraction,
        context=context,
    )
