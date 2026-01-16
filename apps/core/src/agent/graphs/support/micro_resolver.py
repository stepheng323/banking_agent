"""Support micro-resolver.

Bank-grade resolver for support flows:
- Checks requested actions against capabilities
- Determines next support step
- Tracks session context with proper lifecycle
- Decides when to escalate (always with ticket)
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
    ESCALATE = "ESCALATE"         # Create ticket + optionally notify human


class NextStep(str, Enum):
    """What to do next in the support flow."""
    
    ASK_REFERENCE = "ask_reference"
    LOOKUP_TRANSACTION = "lookup_transaction"
    EXPLAIN_STATUS = "explain_status"
    ASK_CLARIFICATION = "ask_clarification"
    CREATE_TICKET = "create_ticket"  # Always the terminal step for escalation


class Prompt(BaseModel):
    """Templated prompt for response."""
    
    key: str
    vars: dict[str, Any] = Field(default_factory=dict)


class NegotiationResult(BaseModel):
    """Structured capability negotiation result."""
    
    missing_actions: list[SupportAction]
    suggested_action: SupportAction | None = None
    alternatives: list[SupportAction] = Field(default_factory=list)
    message: str


class ResolverDecision(BaseModel):
    """Decision from support micro-resolver."""
    
    decision: Decision
    next_step: NextStep
    extraction: SupportExtractionResult
    context: SupportContext
    
    # For NEGOTIATE
    negotiation: NegotiationResult | None = None
    
    # For ESCALATE - always creates ticket, optionally notifies human
    escalation: EscalationResult | None = None
    notify_human: bool = False  # Whether to alert support team
    
    prompts: list[Prompt] = Field(default_factory=list)


ACTION_MAP = {
    RequestedAction.LOOKUP_TRANSACTION: SupportAction.LOOKUP_TRANSACTION,
    RequestedAction.EXPLAIN_STATUS: SupportAction.EXPLAIN_STATUS,
    RequestedAction.RETRY_PAYOUT: SupportAction.RETRY_PAYOUT,
    RequestedAction.INITIATE_REFUND: SupportAction.INITIATE_REFUND,
    RequestedAction.CREATE_TICKET: SupportAction.CREATE_TICKET,
    RequestedAction.ESCALATE: SupportAction.ESCALATE,
}


def _has_transaction_ref(ref: TransactionReference) -> bool:
    """
    Check if we have enough to find the transaction.
    
    Requires:
    - explicit transaction_id, OR
    - quoted message reference, OR
    - recent transaction flag, OR
    - at least 2 of: amount, recipient_name, date_hint
    """
    if ref.transaction_id or ref.use_quoted or ref.use_recent:
        return True
    
    # Require at least 2 signals for fuzzy matching
    signals = sum(bool(x) for x in [ref.amount, ref.recipient_name, ref.date_hint])
    return signals >= 2


def _should_escalate(context: SupportContext, intent: SupportIntent) -> tuple[EscalationResult | None, bool]:
    """
    Determine if we should escalate.
    
    Returns (escalation_result, notify_human)
    Note: ESCALATE always creates a ticket. notify_human is for alerting support team.
    """
    
    # Max attempts reached - ticket + notify
    if context.attempts >= SUPPORT_LIMITS["max_escalation_attempts"]:
        return EscalationResult(
            reason="max_attempts",
            context={"attempts": context.attempts},
        ), True
    
    # Fraud - ticket + definitely notify
    if intent == SupportIntent.FRAUD_REPORT:
        return EscalationResult(
            reason="fraud_suspected",
            transaction_id=context.last_transaction_ref,
        ), True
    
    # User explicitly asks for human
    if intent == SupportIntent.HUMAN_HANDOFF:
        return EscalationResult(
            reason="user_requested",
        ), True
    
    return None, False


def _build_negotiation(missing: list[SupportAction]) -> NegotiationResult:
    """Build structured negotiation result with alternatives."""
    alternatives = []
    for action in missing:
        alt = get_alternative(action)
        if alt and alt not in alternatives:
            alternatives.append(alt)
    
    # Suggested action is the first available alternative
    suggested = alternatives[0] if alternatives else SupportAction.ESCALATE
    
    return NegotiationResult(
        missing_actions=missing,
        suggested_action=suggested,
        alternatives=alternatives,
        message=generate_limitation_message(missing),
    )


def resolve(
    extraction: SupportExtractionResult,
    context: SupportContext | None = None,
    has_quoted_message: bool = False,
) -> ResolverDecision:
    """
    Main micro-resolver entry point.
    
    Flow:
    1. Check if should escalate immediately (fraud, max attempts, user request)
    2. Check if transaction ref is available
    3. Check requested actions against capabilities
    4. Determine next step
    
    Note: attempts are NOT incremented here. The graph should increment
    after seeing handler result (NEEDS_INFO, lookup failure, negotiation rejected).
    """
    if context is None:
        context = SupportContext()
    
    # Update context with this turn's intent
    context.last_issue_intent = extraction.intent
    
    # Handle quoted message
    if has_quoted_message and extraction.transaction_ref:
        extraction.transaction_ref.use_quoted = True
    
    # Check for immediate escalation
    escalation, notify_human = _should_escalate(context, extraction.intent)
    if escalation:
        context.last_support_step = "creating_ticket"
        return ResolverDecision(
            decision=Decision.ESCALATE,
            next_step=NextStep.CREATE_TICKET,  # Always create ticket, not "escalate"
            extraction=extraction,
            context=context,
            escalation=escalation,
            notify_human=notify_human,
            prompts=[Prompt(key="support.creating_ticket", vars={"reason": escalation.reason})],
        )
    
    # Check if we have transaction reference (for intents that need it)
    has_ref = _has_transaction_ref(extraction.transaction_ref)
    
    if not has_ref and extraction.intent not in (SupportIntent.LIMITS_FEES, SupportIntent.ACCOUNT_LINKING):
        context.last_support_step = "asked_for_reference"
        # Don't increment attempts here - wait for next turn's result
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
    
    # Check requested actions against capabilities
    requested = [ACTION_MAP[a] for a in extraction.requested_actions if a in ACTION_MAP]
    missing = check_actions(requested)
    
    if missing:
        negotiation = _build_negotiation(missing)
        
        # Determine next step based on suggested action
        if negotiation.suggested_action == SupportAction.ESCALATE:
            next_step = NextStep.CREATE_TICKET
        else:
            next_step = NextStep.ASK_CLARIFICATION
        
        return ResolverDecision(
            decision=Decision.NEGOTIATE,
            next_step=next_step,
            extraction=extraction,
            context=context,
            negotiation=negotiation,
            prompts=[Prompt(key="support.negotiate", vars={"message": negotiation.message})],
        )
    
    # Route based on intent
    if extraction.intent in (SupportIntent.FAILED_TRANSFER, SupportIntent.PENDING_TRANSFER, 
                             SupportIntent.GENERAL_TX_ISSUE, SupportIntent.TRANSFER_STATUS,
                             SupportIntent.TRANSFER_FAILURE_REASON):
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
    
    if extraction.intent in (SupportIntent.REVERSAL_REFUND, SupportIntent.WRONG_RECIPIENT,
                             SupportIntent.REVERSAL_REFUND_STATUS):
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


def increment_attempts(context: SupportContext) -> SupportContext:
    """
    Increment attempts counter.
    
    Call this from the graph ONLY when:
    - Handler returns NEEDS_INFO
    - Transaction lookup fails
    - Capability negotiation rejected
    """
    context.attempts += 1
    return context


def reset_context_on_resolution(context: SupportContext, ticket_id: str | None = None) -> SupportContext:
    """
    Reset context after successful resolution or ticket creation.
    
    Preserves last_ticket_id and last_transaction_ref for "any update?" queries.
    """
    if ticket_id:
        context.last_ticket_id = ticket_id
    
    # Reset attempts but preserve refs
    context.attempts = 0
    context.last_support_step = "resolved" if not ticket_id else "ticket_created"
    
    return context
