"""Support micro-resolver for bank-grade support flows."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from apps.core.src.agent.graphs.support.capabilities import (
    SUPPORT_LIMITS,
    SupportAction,
    check_actions,
    generate_limitation_message,
    get_alternative,
)
from apps.core.src.agent.graphs.support.models import (
    EscalationResult,
    RequestedAction,
    SupportContext,
    SupportExtractionResult,
    SupportIntent,
    TransactionReference,
)


class Decision(str, Enum):
    PROCEED = "PROCEED"
    NEGOTIATE = "NEGOTIATE"
    COLLECT = "COLLECT"
    ESCALATE = "ESCALATE"


class NextStep(str, Enum):
    ASK_REFERENCE = "ask_reference"
    LOOKUP_TRANSACTION = "lookup_transaction"
    EXPLAIN_STATUS = "explain_status"
    ASK_CLARIFICATION = "ask_clarification"
    CREATE_TICKET = "create_ticket"


class Prompt(BaseModel):
    key: str
    vars: dict[str, Any] = Field(default_factory=dict)


class NegotiationResult(BaseModel):
    missing_actions: list[SupportAction]
    suggested_action: SupportAction | None = None
    alternatives: list[SupportAction] = Field(default_factory=list)
    message: str


class ResolverDecision(BaseModel):
    decision: Decision
    next_step: NextStep
    extraction: SupportExtractionResult
    context: SupportContext
    negotiation: NegotiationResult | None = None
    escalation: EscalationResult | None = None
    notify_human: bool = False
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
    """Check if we have enough to find the transaction."""
    if ref.transaction_id or ref.use_quoted or ref.use_recent:
        return True
    signals = sum(bool(x) for x in [ref.amount, ref.recipient_name, ref.date_hint])
    return signals >= 2


def _should_escalate(context: SupportContext, intent: SupportIntent) -> tuple[EscalationResult | None, bool]:
    """Determine if we should escalate. Returns (escalation_result, notify_human)."""
    if context.attempts >= SUPPORT_LIMITS["max_escalation_attempts"]:
        return EscalationResult(reason="max_attempts", context={"attempts": context.attempts}), True

    if intent == SupportIntent.FRAUD_REPORT:
        return EscalationResult(reason="fraud_suspected", transaction_id=context.last_transaction_ref), True

    if intent == SupportIntent.HUMAN_HANDOFF:
        return EscalationResult(reason="user_requested"), True

    return None, False


def _build_negotiation(missing: list[SupportAction]) -> NegotiationResult:
    """Build structured negotiation result with alternatives."""
    alternatives = []
    for action in missing:
        alt = get_alternative(action)
        if alt and alt not in alternatives:
            alternatives.append(alt)

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
    """Main micro-resolver entry point."""
    if context is None:
        context = SupportContext()

    context.last_issue_intent = extraction.intent

    if has_quoted_message and extraction.transaction_ref:
        extraction.transaction_ref.use_quoted = True

    escalation, notify_human = _should_escalate(context, extraction.intent)
    if escalation:
        context.last_support_step = "creating_ticket"
        return ResolverDecision(
            decision=Decision.ESCALATE,
            next_step=NextStep.CREATE_TICKET,
            extraction=extraction,
            context=context,
            escalation=escalation,
            notify_human=notify_human,
            prompts=[Prompt(key="support.creating_ticket", vars={"reason": escalation.reason})],
        )

    has_ref = _has_transaction_ref(extraction.transaction_ref)

    if not has_ref and extraction.intent not in (SupportIntent.LIMITS_FEES, SupportIntent.ACCOUNT_LINKING):
        context.last_support_step = "asked_for_reference"
        return ResolverDecision(
            decision=Decision.COLLECT,
            next_step=NextStep.ASK_REFERENCE,
            extraction=extraction,
            context=context,
            prompts=[Prompt(key="support.ask_reference", vars={"intent": extraction.intent.value})],
        )

    requested = [ACTION_MAP[a] for a in extraction.requested_actions if a in ACTION_MAP]
    missing = check_actions(requested)

    if missing:
        negotiation = _build_negotiation(missing)
        next_step = NextStep.CREATE_TICKET if negotiation.suggested_action == SupportAction.ESCALATE else NextStep.ASK_CLARIFICATION

        return ResolverDecision(
            decision=Decision.NEGOTIATE,
            next_step=next_step,
            extraction=extraction,
            context=context,
            negotiation=negotiation,
            prompts=[Prompt(key="support.negotiate", vars={"message": negotiation.message})],
        )

    if extraction.intent in (SupportIntent.FAILED_TRANSFER, SupportIntent.PENDING_TRANSFER,
                             SupportIntent.GENERAL_TX_ISSUE, SupportIntent.TRANSFER_STATUS):
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

    context.last_support_step = "explaining"
    return ResolverDecision(
        decision=Decision.PROCEED,
        next_step=NextStep.EXPLAIN_STATUS,
        extraction=extraction,
        context=context,
    )


def increment_attempts(context: SupportContext) -> SupportContext:
    """Increment attempts counter."""
    context.attempts += 1
    return context


def reset_context_on_resolution(context: SupportContext, ticket_id: str | None = None) -> SupportContext:
    """Reset context after resolution or ticket creation."""
    if ticket_id:
        context.last_ticket_id = ticket_id

    context.attempts = 0
    context.last_support_step = "resolved" if not ticket_id else "ticket_created"

    return context
