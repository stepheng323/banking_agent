"""Support Worker.

Stateless worker for Support tasks.
"""

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import SupportOutcome, SupportResult
from apps.chat.src.agent.workers.support.capabilities import SUPPORT_LIMITS
from apps.chat.src.agent.workers.support.classifier import SupportClassifier
from apps.chat.src.agent.workers.support.context_manager import SupportContextManager
from apps.chat.src.agent.workers.support.diagnostic_routing import SupportDiagnosticRouter
from apps.chat.src.agent.workers.support.followups import (
    contextual_followup_reference,
    is_recent_transaction_reference,
    recent_status_priority,
    should_verify_latest_transaction_status,
)
from apps.chat.src.agent.workers.support.handler_dispatch import SupportHandlerDispatcher
from apps.chat.src.agent.workers.support.micro_resolver import (
    NextStep,
)
from apps.chat.src.agent.workers.support.micro_resolver import (
    resolve as micro_resolve,
)
from apps.chat.src.agent.workers.support.models import (
    SupportExtractionResult,
    SupportIntent,
    TransactionReference,
)
from apps.chat.src.agent.workers.support.reference_flow import SupportReferenceFlow
from apps.chat.src.agent.workers.support.resolver import TransactionResolver
from apps.chat.src.agent.workers.support.results import (
    build_receipt_result,
    create_ticket_response,
    result_from_support_response,
    ticket_code_from_message,
)
from banking.support.services.ticket_service import TicketService
from shared.i18n.locale import LocaleManager
from shared.i18n.renderer import render_message
from shared.policy.service import capability_block_message
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
        ticket_service: TicketService | None = None,
        bank_transaction_repo: Any | None = None,
    ) -> None:
        self.llm = llm
        self.classifier = SupportClassifier(llm)
        self.resolver = TransactionResolver(transaction_repo, actionable_message_repo, bank_transaction_repo)
        self.context_manager = SupportContextManager(redis_client)
        self._ticket_service = ticket_service
        self._handler_dispatcher = SupportHandlerDispatcher(
            context_manager=self.context_manager,
            ticket_service=ticket_service,
        )
        self._reference_flow = SupportReferenceFlow(
            context_manager=self.context_manager,
            resolver=self.resolver,
            handler_dispatcher=self._handler_dispatcher,
        )
        self._diagnostic_router = SupportDiagnosticRouter(
            llm=llm,
            resolver=self.resolver,
            context_manager=self.context_manager,
        )

    async def run(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> SupportResult:
        """Run the Support flow."""
        del pin_verified
        phone_number = context.get("phone_number", "")
        user_id = context.get("user_id") or phone_number
        locale = LocaleManager.normalize(context.get("language")).value
        message = (user_message or "").strip()
        if policy_message := capability_block_message(domain="support", action="collect_details", locale=locale):
            logger.info("support_worker_capability_blocked")
            return SupportResult(
                outcome=SupportOutcome.OK,
                response=policy_message,
                final_message=policy_message,
            )

        # Extract inputs from payload
        quoted_message_id = payload.get("quoted_message_id")
        transaction = payload.get("transaction")

        support_ctx = await self.context_manager.get(user_id)
        pending_tx, pending_response = await self._reference_flow.handle_pending_reference_followup(
            user_id=user_id,
            support_ctx=support_ctx,
            message=message,
            locale=locale,
        )
        if pending_response is not None:
            return pending_response
        if pending_tx is not None:
            return build_receipt_result(
                transaction=pending_tx,
                context=context,
                locale=locale,
            )

        try:
            # 1. Classification
            intent_str = payload.get("intent")
            intent = None
            classification = None

            if intent_str:
                try:
                    intent = SupportIntent(intent_str)
                except ValueError:
                    logger.info("support_intent_invalid", provided=intent_str)

            if not intent:
                result = await self.classifier.classify(message)
                classification = result
                if self.classifier.is_support_intent(result):
                    intent = result.intent

            contextual_intent, contextual_ref = contextual_followup_reference(
                support_ctx=support_ctx,
                message=message,
            )
            if contextual_intent is not None:
                intent = intent or contextual_intent

            if not intent:
                return SupportResult(
                    outcome=SupportOutcome.OK,
                    response=render_message("support.not_sure", locale),
                )

            # 2. Extract Transaction Reference
            tx_ref = None
            if classification and classification.transaction_ref:
                tx_ref = TransactionReference(
                    transaction_id=classification.transaction_ref.transaction_id,
                    amount=classification.transaction_ref.amount,
                    recipient_name=classification.transaction_ref.recipient_name,
                    date_hint=classification.transaction_ref.date_hint,
                    use_quoted=classification.transaction_ref.use_quoted,
                    use_recent=classification.transaction_ref.use_recent,
                )
            if quoted_message_id:
                tx_ref = tx_ref or TransactionReference()
                tx_ref.use_quoted = True
            if contextual_ref is not None:
                tx_ref = contextual_ref
            if should_verify_latest_transaction_status(intent, message):
                tx_ref = tx_ref or TransactionReference()
                tx_ref.use_recent = True

            extraction = SupportExtractionResult(
                intent=intent,
                intent_confidence=classification.confidence if classification else 1.0,
                transaction_ref=tx_ref or TransactionReference(),
                raw_issue=message,
            )
            has_explicit_tx_ref = bool(tx_ref and self.resolver._has_explicit_ref(tx_ref))  # type: ignore[attr-defined]

            resolved_tx = transaction
            if (
                resolved_tx is None
                and intent == SupportIntent.RECEIPT_REQUEST
                and not quoted_message_id
                and not has_explicit_tx_ref
            ):
                resolved_tx, receipt_followup = await self._reference_flow.resolve_recent_batch_receipt_reference(
                    user_id=user_id,
                    support_ctx=support_ctx,
                    context=context,
                    message=message,
                    locale=locale,
                )
                if receipt_followup is not None:
                    return receipt_followup

            if (
                resolved_tx is None
                and intent in {SupportIntent.FAILED_TRANSFER, SupportIntent.RETRY_TRANSFER}
                and not quoted_message_id
                and not has_explicit_tx_ref
            ):
                resolved_tx, recent_support_followup = await self._reference_flow.resolve_recent_batch_support_reference(
                    user_id=user_id,
                    support_ctx=support_ctx,
                    context=context,
                    message=message,
                    locale=locale,
                    intent=intent,
                )
                if recent_support_followup is not None:
                    return recent_support_followup
                if resolved_tx is not None:
                    tx_ref = tx_ref or TransactionReference()
                    tx_ref.use_recent = True
                    extraction.transaction_ref.use_recent = True

            if resolved_tx is not None and intent == SupportIntent.RECEIPT_REQUEST:
                return build_receipt_result(
                    transaction=resolved_tx,
                    context=context,
                    locale=locale,
                )

            decision = None
            diagnostic_ticket_code = None
            diagnostic_reason = None
            diagnostic_route = await self._diagnostic_router.route(
                support_ctx=support_ctx,
                context=context,
                message=message,
                locale=locale,
                intent=intent,
                classification=classification,
                extraction=extraction,
                tx_ref=tx_ref,
                resolved_tx=resolved_tx if isinstance(resolved_tx, dict) else None,
                quoted_message_id=quoted_message_id,
                ticket_code=ticket_code_from_message(message),
            )
            if diagnostic_route is not None and diagnostic_route.get("result") is not None:
                return diagnostic_route["result"]

            if diagnostic_route is not None:
                intent = diagnostic_route["intent"]
                tx_ref = diagnostic_route.get("transaction_ref") or tx_ref
                extraction.intent = intent
                if tx_ref is not None:
                    extraction.transaction_ref = tx_ref
                support_ctx.last_issue_intent = intent
                await self.context_manager.save(user_id, support_ctx)
                next_step = diagnostic_route["next_step"]
                diagnostic_ticket_code = diagnostic_route.get("ticket_code")
                diagnostic_reason = diagnostic_route.get("reason")
            else:
                # 3. Micro-Resolution (Context Aware)
                decision = micro_resolve(
                    extraction=extraction,
                    context=support_ctx,
                    has_quoted_message=bool(quoted_message_id),
                    language=locale,
                )
                await self.context_manager.save(user_id, decision.context)
                next_step = decision.next_step

            # 4. Handle Routing
            if next_step == NextStep.ASK_REFERENCE:
                return SupportResult(
                    outcome=SupportOutcome.NEEDS_INPUT,
                    response=render_message("support.ask_reference", locale),
                )
            elif next_step == NextStep.ASK_CLARIFICATION:
                await self.context_manager.increment_attempts(user_id)
                prompt = render_message("support.ask_clarification", locale)
                if decision.prompts:
                    if decision.prompts[0].key == "support.negotiate":
                        prompt = decision.negotiation.message if decision.negotiation else prompt
                return SupportResult(
                    outcome=SupportOutcome.NEEDS_INPUT,
                    response=prompt,
                )

            if next_step == NextStep.LOOKUP_TICKET:
                response = await self._handler_dispatcher.dispatch(
                    intent,
                    resolved_tx,
                    user_id=user_id,
                    locale=locale,
                    ticket_code=diagnostic_ticket_code or ticket_code_from_message(message),
                )
                return result_from_support_response(response, intent=intent, locale=locale)

            # 5. Transaction Resolution
            if not resolved_tx and next_step in (
                NextStep.LOOKUP_TRANSACTION,
                NextStep.EXPLAIN_STATUS,
            ):
                tx_obj, method = await self.resolver.resolve(
                    user_id,
                    tx_ref,
                    quoted_message_id,
                    recent_status_priority=recent_status_priority(intent),
                    prefer_latest_recent=(
                        should_verify_latest_transaction_status(intent, message)
                        or is_recent_transaction_reference(message)
                    ),
                )
                if tx_obj:
                    resolved_tx = self.resolver.transaction_to_dict(tx_obj)

                if not tx_obj and method == "not_found":
                    updated_context = await self.context_manager.increment_attempts(user_id)
                    # Start linear escalation after max attempts -> handled next time or via escalation logic
                    if updated_context.attempts >= SUPPORT_LIMITS["max_escalation_attempts"]:
                        return await create_ticket_response(
                            user_id=user_id,
                            intent=intent,
                            transaction=None,
                            reason="tx_not_found_max_attempts",
                            context_manager=self.context_manager,
                            ticket_service=self._ticket_service,
                            locale=locale,
                        )

                    return SupportResult(
                        outcome=SupportOutcome.OK,
                        response=render_message("support.tx_not_found", locale),
                    )

                if not tx_obj and method == "ambiguous":
                    return SupportResult(
                        outcome=SupportOutcome.NEEDS_INPUT,
                        response=render_message("support.tx_ambiguous", locale),
                    )

            # 6. Dispatch to Handler
            if next_step == NextStep.CREATE_TICKET:
                reason = diagnostic_reason or (
                    decision.escalation.reason if decision and decision.escalation else "micro_resolver_escalation"
                )
                return await create_ticket_response(
                    user_id=user_id,
                    intent=intent,
                    transaction=resolved_tx,
                    reason=reason,
                    context_manager=self.context_manager,
                    ticket_service=self._ticket_service,
                    locale=locale,
                )

            if resolved_tx or intent in (SupportIntent.TICKET_STATUS, SupportIntent.FRAUD_REPORT):
                if intent == SupportIntent.RECEIPT_REQUEST and isinstance(resolved_tx, dict):
                    return build_receipt_result(
                        transaction=resolved_tx,
                        context=context,
                        locale=locale,
                    )
                response = await self._handler_dispatcher.dispatch(intent, resolved_tx, user_id=user_id, locale=locale)

                if response and response.next_step == "NEEDS_INFO":
                    await self.context_manager.increment_attempts(user_id)

                if isinstance(resolved_tx, dict):
                    support_ctx.pending_reference = None
                    support_ctx.last_transaction_ref = str(
                        resolved_tx.get("id") or resolved_tx.get("transaction_id") or support_ctx.last_transaction_ref or ""
                    )
                    await self.context_manager.save(user_id, support_ctx)

                return result_from_support_response(response, intent=intent, locale=locale)

            return SupportResult(
                outcome=SupportOutcome.OK,
                response=render_message("support.need_tx_reference", locale),
            )

        except Exception as e:
            logger.error(f"Support worker failed: {e}", exc_info=True)
            return SupportResult(outcome=SupportOutcome.FAILED, error=str(e))
