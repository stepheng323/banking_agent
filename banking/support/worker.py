"""Support Worker.

Stateless worker for Support tasks.
"""

from typing import Any

from banking.policy.service import capability_block_message
from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import SupportOutcome, SupportResult
from banking.support.capabilities import SUPPORT_LIMITS
from banking.support.classifier import SupportClassifier
from banking.support.context_manager import SupportContextManager
from banking.support.diagnostic_routing import SupportDiagnosticRouter
from banking.support.followups import (
    contextual_followup_reference,
    is_recent_transaction_reference,
    recent_status_priority,
    should_verify_latest_transaction_status,
)
from banking.support.handler_dispatch import SupportHandlerDispatcher
from banking.support.handlers.status_utils import resolve_transaction_status
from banking.support.micro_resolver import (
    NextStep,
)
from banking.support.micro_resolver import (
    resolve as micro_resolve,
)
from banking.support.models import (
    SupportExtractionResult,
    SupportIntent,
    TransactionReference,
)
from banking.support.reference_flow import SupportReferenceFlow
from banking.support.resolver import TransactionResolver
from banking.support.results import (
    build_receipt_result,
    create_ticket_response,
    result_from_support_response,
    ticket_code_from_message,
)
from banking.support.services.ticket_service import TicketService
from shared.types.read import ReadRequest, ReadResult, normalize_read_request
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _transaction_reference_id(transaction: Any) -> str | None:
    if not isinstance(transaction, dict):
        return None
    for key in ("transaction_id", "id", "reference", "transaction_reference", "local_transaction_id"):
        value = transaction.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


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
        action = str(payload.get("action") or "handle_request").strip().lower()
        if action in {
            "list_support_tickets",
            "find_support_ticket",
            "append_support_ticket_note",
            "close_support_ticket",
        }:
            return await self._run_ticket_operation(
                action=action,
                payload=payload,
                user_id=str(user_id),
                locale=locale,
                channel=str(context.get("channel") or "unknown"),
            )
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
            if isinstance(transaction, dict):
                tx_ref = tx_ref or TransactionReference()
                if not tx_ref.transaction_id:
                    tx_ref.transaction_id = _transaction_reference_id(transaction)

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
                (
                    resolved_tx,
                    recent_support_followup,
                ) = await self._reference_flow.resolve_recent_batch_support_reference(
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
            if (
                isinstance(resolved_tx, dict)
                and intent == SupportIntent.REVERSAL_REFUND
                and resolve_transaction_status(resolved_tx) == "successful"
            ):
                response = await self._handler_dispatcher.dispatch(intent, resolved_tx, user_id=user_id, locale=locale)
                support_ctx.pending_reference = None
                support_ctx.last_transaction_ref = str(
                    resolved_tx.get("id") or resolved_tx.get("transaction_id") or support_ctx.last_transaction_ref or ""
                )
                await self.context_manager.save(user_id, support_ctx)
                return result_from_support_response(response, intent=intent, locale=locale)

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
                if decision is not None and decision.prompts:
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
                    resolved_tx = tx_obj if isinstance(tx_obj, dict) else self.resolver.transaction_to_dict(tx_obj)

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
                        resolved_tx.get("id")
                        or resolved_tx.get("transaction_id")
                        or support_ctx.last_transaction_ref
                        or ""
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

    async def _run_ticket_operation(
        self,
        *,
        action: str,
        payload: dict[str, Any],
        user_id: str,
        locale: str,
        channel: str,
    ) -> SupportResult:
        if self._ticket_service is None:
            return SupportResult(
                outcome=SupportOutcome.FAILED,
                error=render_message("support.ticket.unavailable", locale),
            )
        policy_action = {
            "list_support_tickets": "lookup_ticket",
            "find_support_ticket": "lookup_ticket",
            "append_support_ticket_note": "update_ticket",
            "close_support_ticket": "close_ticket",
        }[action]
        if message := capability_block_message(domain="support", action=policy_action, locale=locale):
            return SupportResult(outcome=SupportOutcome.OK, response=message)

        ticket_id = str(payload.get("ticket_id") or "").strip() or None
        ticket_code = str(payload.get("ticket_code") or "").strip() or None
        request = normalize_read_request(payload)
        if action == "list_support_tickets":
            request = request or ReadRequest(subject="ticket", response_shape="surface_list")
            tickets, total_count = await self._ticket_service.list_open_page(
                user_id,
                limit=request.page_size + 1,
                offset=request.offset,
            )
            page = tickets[: request.page_size]
            read_result = ReadResult(
                request=request,
                total_count=total_count,
                returned_count=0 if request.response_shape.startswith("fact_") else len(page),
                has_next=len(tickets) > request.page_size or request.offset + request.page_size < total_count,
                has_previous=request.offset > 0,
            )
            if request.response_shape == "fact_count":
                response = render_message("support.ticket.count", locale, {"count": total_count})
            elif request.response_shape == "fact_bool":
                response = render_message(
                    "support.ticket.exists_yes" if total_count else "support.ticket.exists_no",
                    locale,
                )
            elif not page:
                response = render_message("support.ticket.list_empty", locale)
            else:
                lines = [render_message("support.ticket.list_header", locale)]
                items: list[dict[str, Any]] = []
                for index, ticket in enumerate(page, start=1):
                    lines.append(
                        render_message(
                            "support.ticket.list_row",
                            locale,
                            {
                                "index": index,
                                "code": ticket.ticket_code,
                                "status": ticket.status,
                                "summary": ticket.summary,
                            },
                        )
                    )
                    items.append(
                        {
                            "id": str(ticket.id),
                            "code": ticket.ticket_code,
                            "status": ticket.status,
                            "summary": ticket.summary,
                            "priority": ticket.priority,
                            "version_token": ticket.updated_at.isoformat(),
                            "display_label": f"{ticket.ticket_code} · {ticket.status}",
                        }
                    )
                if read_result.has_next:
                    lines.extend(["", render_message("common.pagination.more", locale)])
                response = "\n".join(lines)
                return SupportResult(
                    outcome=SupportOutcome.OK,
                    response=response,
                    details={"viewed_support_tickets": items},
                    read_result=read_result,
                )
            return SupportResult(outcome=SupportOutcome.OK, response=response, read_result=read_result)

        if not ticket_id and not ticket_code:
            return SupportResult(
                outcome=SupportOutcome.NEEDS_INPUT,
                response=render_message("support.ticket.select", locale),
            )
        selected_ticket = await self._ticket_service.get_user_ticket(
            user_id,
            ticket_id=ticket_id,
            ticket_code=ticket_code,
        )
        if selected_ticket is None:
            return SupportResult(
                outcome=SupportOutcome.OK,
                response=render_message(
                    "support.ticket.not_found",
                    locale,
                    {"ticket_code": ticket_code or ""},
                ),
            )
        ticket = selected_ticket

        if action == "find_support_ticket":
            request = request or ReadRequest(subject="ticket", response_shape="surface_detail")
            if request.response_shape == "fact_status":
                response = render_message(
                    "support.ticket.status_fact",
                    locale,
                    {"code": ticket.ticket_code, "status": ticket.status},
                )
            else:
                response = render_message(
                    "support.ticket.detail",
                    locale,
                    {
                        "code": ticket.ticket_code,
                        "status": ticket.status,
                        "summary": ticket.summary,
                        "priority": ticket.priority,
                    },
                )
            return SupportResult(
                outcome=SupportOutcome.OK,
                response=response,
                read_result=ReadResult(
                    request=request,
                    total_count=1,
                    returned_count=0 if request.response_shape.startswith("fact_") else 1,
                ),
            )

        if action == "append_support_ticket_note":
            note = str(payload.get("ticket_note") or "").strip()
            if not note or len(note) > 1000:
                return SupportResult(
                    outcome=SupportOutcome.NEEDS_INPUT,
                    response=render_message("support.ticket.note_prompt", locale),
                )
            updated = await self._ticket_service.append_user_note(
                user_id,
                ticket_id=str(ticket.id),
                note=note,
                channel=channel,
            )
            if updated is None:
                return SupportResult(
                    outcome=SupportOutcome.OK,
                    response=render_message("support.ticket.note_closed", locale),
                )
            return SupportResult(
                outcome=SupportOutcome.OK,
                response=render_message("support.ticket.note_added", locale, {"code": updated.ticket_code}),
            )

        if ticket.status == "closed":
            return SupportResult(
                outcome=SupportOutcome.OK,
                response=render_message("support.ticket.already_closed", locale, {"code": ticket.ticket_code}),
            )
        confirmation = payload.get("confirmation")
        confirmed = isinstance(confirmation, dict) and confirmation.get("confirmed") is True
        version_token = ticket.updated_at.isoformat()
        snapshot = {
            "ticket_id": str(ticket.id),
            "ticket_code": ticket.ticket_code,
            "version_token": version_token,
        }
        if not confirmed:
            return SupportResult(
                outcome=SupportOutcome.NEEDS_CONFIRMATION,
                confirmation_summary=render_message(
                    "support.ticket.close_review",
                    locale,
                    {"code": ticket.ticket_code, "summary": ticket.summary},
                ),
                confirmation_snapshot=snapshot,
            )
        expected_version = None
        if isinstance(confirmation, dict):
            raw_snapshot = confirmation.get("snapshot")
            if isinstance(raw_snapshot, dict):
                expected_version = str(raw_snapshot.get("version_token") or "") or None
        closed, already_closed, stale = await self._ticket_service.close_user_ticket(
            user_id,
            ticket_id=str(ticket.id),
            expected_version=expected_version or version_token,
        )
        if stale:
            return SupportResult(
                outcome=SupportOutcome.FAILED,
                error=render_message("support.ticket.stale", locale),
            )
        if closed is None:
            return SupportResult(
                outcome=SupportOutcome.OK,
                response=render_message(
                    "support.ticket.not_found",
                    locale,
                    {"ticket_code": ticket_code or ""},
                ),
            )
        return SupportResult(
            outcome=SupportOutcome.OK,
            response=render_message(
                "support.ticket.already_closed" if already_closed else "support.ticket.closed",
                locale,
                {"code": closed.ticket_code},
            ),
        )
