"""Support Worker.

Stateless worker for Support tasks using LangGraph.
"""

from typing import Any

from apps.core.src.agent.graphs.support.classifier import SupportClassifier
from apps.core.src.agent.graphs.support.context_manager import SupportContextManager
from apps.core.src.agent.graphs.support.handlers import (
    handle_failure_reason,
    handle_fraud,
    handle_pending,
    handle_receipt_request,
    handle_retry,
    handle_reversal_status,
    handle_ticket_status,
    handle_transfer_status,
    handle_wrong_debit,
)
from apps.core.src.agent.graphs.support.handlers.escalation import handle_escalation
from apps.core.src.agent.graphs.support.micro_resolver import (
    NextStep,
)
from apps.core.src.agent.graphs.support.micro_resolver import (
    resolve as micro_resolve,
)
from apps.core.src.agent.graphs.support.models import (
    SupportExtractionResult,
    SupportIntent,
    SupportResponse,
    TransactionReference,
)
from apps.core.src.agent.graphs.support.resolver import TransactionResolver
from apps.core.src.agent.orchestrator.models.domain import SupportOutcome, SupportResult
from shared.repositories.support_ticket_repository import SupportTicketRepository
from shared.services.ticket_service import TicketService
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class SupportWorker:
    """Stateless worker for Support tasks."""

    def __init__(
        self,
        llm: Any,
        transaction_repo: Any,
        actionable_message_repo: Any,
        redis_client: Any,
        db_session: Any = None,
    ) -> None:
        self.llm = llm
        self.classifier = SupportClassifier(llm)
        self.resolver = TransactionResolver(transaction_repo, actionable_message_repo)
        self.context_manager = SupportContextManager(redis_client)

        self._ticket_service = None
        if db_session:
            ticket_repo = SupportTicketRepository(db_session)
            self._ticket_service = TicketService(ticket_repo)

    async def run(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> SupportResult:
        """Run the Support flow."""
        phone_number = context.get("phone_number", "")
        user_id = context.get("user_id") or phone_number
        message = (user_message or "").strip()

        # Extract inputs from payload
        quoted_message_id = payload.get("quoted_message_id")
        transaction = payload.get("transaction")

        support_ctx = await self.context_manager.get(user_id)

        try:
            # 1. Classification
            intent = payload.get("intent")
            classification = None
            if not intent:
                result = await self.classifier.classify(message)
                intent = result.intent
                classification = result

            if not intent:
                return SupportResult(
                    outcome=SupportOutcome.OK, response="I'm not sure how to help with that support request."
                )

            # 2. Extract Transaction Reference
            tx_ref = None
            if classification and classification.transaction_ref:
                tx_ref = TransactionReference(
                    amount=classification.transaction_ref.amount,
                    recipient_name=classification.transaction_ref.recipient_name,
                    date_hint=classification.transaction_ref.date_hint,
                )
            if quoted_message_id:
                tx_ref = tx_ref or TransactionReference()
                tx_ref.use_quoted = True

            extraction = SupportExtractionResult(
                intent=intent,
                intent_confidence=classification.confidence if classification else 1.0,
                transaction_ref=tx_ref,
                raw_issue=message,
            )

            # 3. Micro-Resolution (Context Aware)
            decision = micro_resolve(
                extraction=extraction,
                context=support_ctx,
                has_quoted_message=bool(quoted_message_id),
            )
            await self.context_manager.save(user_id, decision.context)

            next_step = decision.next_step

            # 4. Handle Routing
            if next_step == NextStep.ASK_REFERENCE:
                return SupportResult(
                    outcome=SupportOutcome.NEEDS_INPUT,
                    response="Which transaction are you asking about?",
                )
            elif next_step == NextStep.ASK_CLARIFICATION:
                await self.context_manager.increment_attempts(user_id)
                prompt = "Could you provide more details?"
                if decision.prompts:
                    if decision.prompts[0].key == "support.negotiate":
                        prompt = decision.negotiation.message if decision.negotiation else prompt
                return SupportResult(
                    outcome=SupportOutcome.NEEDS_INPUT,
                    response=prompt,
                )

            # 5. Transaction Resolution
            resolved_tx = transaction
            if not resolved_tx and next_step in (
                NextStep.LOOKUP_TRANSACTION,
                NextStep.EXPLAIN_STATUS,
                NextStep.RESOLVE_TRANSACTION,
            ):
                tx_obj, method = await self.resolver.resolve(user_id, tx_ref, quoted_message_id)
                if tx_obj:
                    resolved_tx = self.resolver.transaction_to_dict(tx_obj)

                if not tx_obj and method == "not_found":
                    await self.context_manager.increment_attempts(user_id)
                    # Start linear escalation after max attempts -> handled next time or via escalation logic
                    if decision.context.attempts >= 3:
                        return await self._create_ticket_response(user_id, intent, None, "tx_not_found_max_attempts")

                    return SupportResult(
                        outcome=SupportOutcome.OK, response="I couldn't find a recent transaction matching your query."
                    )

                if not tx_obj and method == "ambiguous":
                    return SupportResult(
                        outcome=SupportOutcome.NEEDS_INPUT, response="Which transaction? Please be more specific."
                    )

            # 6. Dispatch to Handler
            if next_step == NextStep.CREATE_TICKET:
                reason = decision.escalation.reason if decision.escalation else "micro_resolver_escalation"
                return await self._create_ticket_response(user_id, intent, resolved_tx, reason)

            if resolved_tx or intent in (SupportIntent.TICKET_STATUS, SupportIntent.FRAUD_REPORT):
                response = await self._dispatch_handler(intent, resolved_tx)

                if response and response.next_step == "NEEDS_INFO":
                    await self.context_manager.increment_attempts(user_id)

                final_msg = response.message if response else "Unable to process."
                return SupportResult(
                    outcome=SupportOutcome.OK,
                    response=final_msg,
                    final_message=final_msg,
                )

            return SupportResult(
                outcome=SupportOutcome.OK, response="I need a transaction reference to help with that."
            )

        except Exception as e:
            logger.error(f"Support worker failed: {e}", exc_info=True)
            return SupportResult(outcome=SupportOutcome.FAILED, error=str(e))

    async def _create_ticket_response(self, user_id: str, intent: Any, transaction: Any, reason: str) -> SupportResult:
        if not self._ticket_service:
            return SupportResult(
                outcome=SupportOutcome.OK, response="I'm escalating this to support, but ticket service is unavailable."
            )

        resp = await handle_escalation(
            user_id=user_id,
            intent=intent.value if hasattr(intent, "value") else str(intent),
            ticket_service=self._ticket_service,
            transaction=transaction,
            reason=reason,
        )

        ticket_code = None
        if resp.escalation and resp.escalation.context:
            ticket_code = resp.escalation.context.get("ticket_code")

        await self.context_manager.reset_on_resolution(
            user_id=user_id,
            ticket_id=ticket_code,
            transaction_ref=transaction.get("transaction_id") if transaction else None,
        )

        return SupportResult(
            outcome=SupportOutcome.OK, response=resp.message, ticket_code=ticket_code, escalation=resp.escalation
        )

    async def _dispatch_handler(self, intent: Any, transaction: Any) -> SupportResponse:
        """Dispatch to handlers."""
        # Reuse the logic from SupportFlowGraph._dispatch_handler
        # Map intents to handler functions
        if intent == SupportIntent.TICKET_STATUS:
            # Ticket status needs context
            ctx = await self.context_manager.get(transaction.get("user_id") if transaction else "")
            if not self._ticket_service:
                return SupportResponse(message="No ticket access.")
            return await handle_ticket_status(
                user_id="",  # Handled inside if needed, or pass from context
                ticket_service=self._ticket_service,
                last_ticket_id=ctx.last_ticket_id,
            )

        if intent == SupportIntent.TRANSFER_STATUS:
            return await handle_transfer_status(transaction)
        elif intent == SupportIntent.PENDING_TRANSFER:
            return await handle_pending(transaction)
        elif intent == SupportIntent.FAILED_TRANSFER:
            return await handle_failure_reason(transaction)
        elif intent == SupportIntent.WRONG_DEBIT:
            return await handle_wrong_debit(transaction)
        elif intent == SupportIntent.REVERSAL_REFUND or intent == SupportIntent.WRONG_RECIPIENT:
            return await handle_reversal_status(transaction)
        elif intent == SupportIntent.RETRY_TRANSFER:
            return await handle_retry(transaction)
        elif intent == SupportIntent.FRAUD_REPORT:
            return await handle_fraud(transaction)
        elif intent == SupportIntent.RECEIPT_REQUEST:
            return await handle_receipt_request(transaction)
        elif intent == SupportIntent.GENERAL_TX_ISSUE:
            return await handle_transfer_status(transaction)
        else:
            return SupportResponse(message="I couldn't understand your request.")
