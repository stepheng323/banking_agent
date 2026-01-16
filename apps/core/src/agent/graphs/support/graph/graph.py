"""SupportFlowGraph - LangGraph for transaction support queries.

v2 Architecture:
- classify (LLM) → micro_resolve (code) → resolve_transaction → handle → respond
- SupportContext persisted to Redis for session continuity
- All escalations create tickets
"""

from typing import Any

import redis.asyncio as redis
from langchain_core.runnables import Runnable, RunnableConfig
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from apps.core.src.agent.graphs.support.classifier import SupportClassifier
from apps.core.src.agent.graphs.support.context_manager import SupportContextManager
from apps.core.src.agent.graphs.support.graph.state import SupportGraphState
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
    Decision,
    NextStep,
    resolve as micro_resolve,
)
from apps.core.src.agent.graphs.support.models import (
    SupportContext,
    SupportExtractionResult,
    SupportIntent,
    SupportResponse,
    TransactionReference,
)
from apps.core.src.agent.graphs.support.resolver import TransactionResolver
from shared.config.settings import settings
from shared.repositories.actionable_message_repository import ActionableMessageRepository
from shared.repositories.support_ticket_repository import SupportTicketRepository
from shared.repositories.transaction_repository import TransactionRepository
from shared.services.ticket_service import TicketService
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def route_after_classify(state: SupportGraphState) -> str:
    """Route based on classification result."""
    if not state.get("intent"):
        return "exit_not_support"
    
    # TICKET_STATUS doesn't need micro-resolver or tx lookup
    if state.get("intent") == SupportIntent.TICKET_STATUS:
        return "check_ticket_status"
    
    return "micro_resolve"


def route_after_micro_resolve(state: SupportGraphState) -> str:
    """Route based on micro-resolver decision."""
    decision = state.get("resolver_decision")
    if not decision:
        return "exit_not_support"
    
    next_step = decision.next_step
    
    if next_step == NextStep.ASK_REFERENCE:
        return "clarify"
    elif next_step == NextStep.LOOKUP_TRANSACTION:
        return "resolve_transaction"
    elif next_step == NextStep.CREATE_TICKET:
        return "create_ticket"
    elif next_step == NextStep.EXPLAIN_STATUS:
        return "resolve_transaction"  # Need tx first
    elif next_step == NextStep.ASK_CLARIFICATION:
        return "clarify"
    else:
        return "resolve_transaction"


def route_after_resolve(state: SupportGraphState) -> str:
    """Route based on transaction resolution."""
    if state.get("needs_clarification"):
        return "clarify"
    if not state.get("transaction") and state.get("intent") not in (
        SupportIntent.FRAUD_REPORT,
        SupportIntent.FRAUD_SUSPECTED,
    ):
        return "no_transaction"
    return "handle"


class SupportFlowGraph:
    """LangGraph-based support flow with micro-resolver and ticketing."""

    def __init__(
        self,
        llm: Runnable,
        transaction_repo: TransactionRepository,
        actionable_message_repo: ActionableMessageRepository,
        redis_client: redis.Redis,
        db_session: Session | None = None,
    ):
        self.llm = llm
        self.redis = redis_client
        self.classifier = SupportClassifier(llm)
        self.resolver = TransactionResolver(transaction_repo, actionable_message_repo)
        self.context_manager = SupportContextManager(redis_client)
        
        # Ticket service (created per-request if db_session provided)
        self._db_session = db_session
        self._ticket_service = None
        if db_session:
            ticket_repo = SupportTicketRepository(db_session)
            self._ticket_service = TicketService(ticket_repo)

        self._graph = None
        self._checkpointer = None
        self._checkpointer_setup = False

    def _get_config(self, phone_number: str) -> RunnableConfig:
        """Get LangGraph config for a user."""
        return {"configurable": {"thread_id": f"support:{phone_number}"}}

    async def clear_checkpoint(self, phone_number: str) -> None:
        """Clear support flow checkpoint for a user."""
        try:
            await self._ensure_checkpointer()
            config = self._get_config(phone_number)
            if self._checkpointer:
                thread_id = config["configurable"]["thread_id"]
                await self._checkpointer.adelete_thread(thread_id)
                logger.info(f"Cleared support checkpoint for {phone_number}")
        except Exception as e:
            logger.error(f"Error clearing support checkpoint: {e}")

    async def _ensure_checkpointer(self) -> None:
        """Ensure checkpointer is initialized and graph is compiled."""
        if not self._checkpointer_setup:
            self._checkpointer = AsyncRedisSaver(redis_url=settings.redis_url)
            await self._checkpointer.asetup()
            self._checkpointer_setup = True

        if self._graph is None:
            self._graph = self._build_graph().compile(checkpointer=self._checkpointer)

    def _build_graph(self) -> StateGraph:
        """Build the support flow graph."""
        graph = StateGraph(SupportGraphState)

        # Nodes
        graph.add_node("classify", self._classify_node)
        graph.add_node("micro_resolve", self._micro_resolve_node)
        graph.add_node("resolve_transaction", self._resolve_transaction_node)
        graph.add_node("handle", self._handle_node)
        graph.add_node("create_ticket", self._create_ticket_node)
        graph.add_node("check_ticket_status", self._check_ticket_status_node)
        graph.add_node("clarify", self._clarify_node)
        graph.add_node("no_transaction", self._no_transaction_node)
        graph.add_node("exit_not_support", self._exit_not_support_node)

        # Entry
        graph.set_entry_point("classify")

        # Edges
        graph.add_conditional_edges(
            "classify",
            route_after_classify,
            {
                "micro_resolve": "micro_resolve",
                "check_ticket_status": "check_ticket_status",
                "exit_not_support": "exit_not_support",
            },
        )

        graph.add_conditional_edges(
            "micro_resolve",
            route_after_micro_resolve,
            {
                "clarify": "clarify",
                "resolve_transaction": "resolve_transaction",
                "create_ticket": "create_ticket",
            },
        )

        graph.add_conditional_edges(
            "resolve_transaction",
            route_after_resolve,
            {
                "handle": "handle",
                "clarify": "clarify",
                "no_transaction": "no_transaction",
            },
        )

        graph.add_edge("handle", END)
        graph.add_edge("create_ticket", END)
        graph.add_edge("check_ticket_status", END)
        graph.add_edge("clarify", END)
        graph.add_edge("no_transaction", END)
        graph.add_edge("exit_not_support", END)

        return graph

    async def _classify_node(self, state: SupportGraphState) -> dict[str, Any]:
        """Classify the user message into a support intent."""
        message = state.get("message", "")
        result = await self.classifier.classify(message)

        if not result.intent:
            return {
                "intent": None,
                "classification": result,
            }

        # Build v2 extraction result
        tx_ref = TransactionReference()
        if result.transaction_ref:
            tx_ref = TransactionReference(
                amount=result.transaction_ref.amount,
                recipient_name=result.transaction_ref.recipient_name,
                date_hint=result.transaction_ref.date_hint,
            )
        
        # Check for quoted message
        if state.get("quoted_message_id"):
            tx_ref.use_quoted = True

        extraction = SupportExtractionResult(
            intent=result.intent,
            intent_confidence=result.confidence,
            transaction_ref=tx_ref,
            raw_issue=message,
        )

        return {
            "intent": result.intent,
            "classification": result,
            "extraction": extraction,
        }

    async def _micro_resolve_node(self, state: SupportGraphState) -> dict[str, Any]:
        """Run micro-resolver to determine next step."""
        extraction = state.get("extraction")
        user_id = state.get("user_id", "")
        
        if not extraction:
            return {"resolver_decision": None}

        # Get context from Redis
        context = await self.context_manager.get(user_id)
        has_quoted = bool(state.get("quoted_message_id"))

        # Run micro-resolver
        decision = micro_resolve(
            extraction=extraction,
            context=context,
            has_quoted_message=has_quoted,
        )

        # Save updated context
        await self.context_manager.save(user_id, decision.context)

        return {
            "resolver_decision": decision,
            "decision": decision.decision,
            "next_step": decision.next_step,
            "support_context": decision.context,
            "notify_human": getattr(decision, "notify_human", False),
        }

    async def _resolve_transaction_node(self, state: SupportGraphState) -> dict[str, Any]:
        """Resolve which transaction the user is referring to."""
        if state.get("transaction"):
            return {
                "transaction": state["transaction"],
                "resolution_method": "pre_resolved",
                "needs_clarification": False,
            }

        user_id = state.get("user_id", "")
        classification = state.get("classification")
        quoted_message_id = state.get("quoted_message_id")

        tx_ref = classification.transaction_ref if classification else None
        tx, method = await self.resolver.resolve(user_id, tx_ref, quoted_message_id)

        if tx:
            return {
                "transaction": self.resolver.transaction_to_dict(tx),
                "transaction_id": str(tx.id),
                "resolution_method": method,
                "needs_clarification": False,
            }

        if method == "not_found":
            # Increment attempts on lookup failure
            await self.context_manager.increment_attempts(user_id)
            return {
                "transaction": None,
                "resolution_method": method,
                "needs_clarification": False,
            }

        return {
            "transaction": None,
            "resolution_method": "ambiguous",
            "needs_clarification": True,
            "clarification_question": "Which transaction are you asking about? Please share more details.",
        }

    async def _handle_node(self, state: SupportGraphState) -> dict[str, Any]:
        """Route to appropriate handler based on intent."""
        intent = state.get("intent")
        transaction = state.get("transaction")

        response = await self._dispatch_handler(intent, transaction)

        # Check if handler needs more info - increment attempts
        if response and response.next_step == "NEEDS_INFO":
            user_id = state.get("user_id", "")
            await self.context_manager.increment_attempts(user_id)

        return {
            "response": response,
            "escalation": response.escalation if response else None,
            "final_message": response.message if response else "Unable to process your request.",
        }

    async def _dispatch_handler(
        self,
        intent: SupportIntent | None,
        transaction: dict[str, Any] | None,
    ) -> SupportResponse:
        """Dispatch to the appropriate handler."""
        if intent == SupportIntent.TRANSFER_STATUS:
            return await handle_transfer_status(transaction)
        elif intent == SupportIntent.PENDING_TRANSFER:
            return await handle_pending(transaction)
        elif intent == SupportIntent.TRANSFER_FAILURE_REASON:
            return await handle_failure_reason(transaction)
        elif intent == SupportIntent.FAILED_TRANSFER:
            return await handle_failure_reason(transaction)
        elif intent == SupportIntent.WRONG_DEBIT:
            return await handle_wrong_debit(transaction)
        elif intent == SupportIntent.REVERSAL_REFUND_STATUS:
            return await handle_reversal_status(transaction)
        elif intent == SupportIntent.REVERSAL_REFUND:
            return await handle_reversal_status(transaction)
        elif intent == SupportIntent.RETRY_TRANSFER:
            return await handle_retry(transaction)
        elif intent in (SupportIntent.FRAUD_SUSPECTED, SupportIntent.FRAUD_REPORT):
            return await handle_fraud(transaction)
        elif intent == SupportIntent.RECEIPT_REQUEST:
            return await handle_receipt_request(transaction)
        elif intent == SupportIntent.GENERAL_TX_ISSUE:
            return await handle_transfer_status(transaction)
        else:
            return SupportResponse(message="I couldn't understand your request.")

    async def _create_ticket_node(self, state: SupportGraphState) -> dict[str, Any]:
        """Create a support ticket."""
        user_id = state.get("user_id", "")
        intent = state.get("intent")
        transaction = state.get("transaction")
        resolver_decision = state.get("resolver_decision")
        
        reason = ""
        if resolver_decision and resolver_decision.escalation:
            reason = resolver_decision.escalation.reason
        
        if not self._ticket_service:
            logger.warning("ticket_service_not_available")
            return {
                "final_message": "I'm escalating this to our support team. They'll be in touch soon.",
                "ticket_code": None,
            }

        response = await handle_escalation(
            user_id=user_id,
            intent=intent.value if intent else "general_tx_issue",
            ticket_service=self._ticket_service,
            transaction=transaction,
            reason=reason,
            notify_human=state.get("notify_human", False),
        )

        # Reset context after ticket creation
        ticket_code = None
        if response.escalation and response.escalation.context:
            ticket_code = response.escalation.context.get("ticket_code")
        
        await self.context_manager.reset_on_resolution(
            user_id=user_id,
            ticket_id=ticket_code,
            transaction_ref=transaction.get("transaction_id") if transaction else None,
        )

        return {
            "final_message": response.message,
            "ticket_code": ticket_code,
            "escalation": response.escalation,
        }

    async def _check_ticket_status_node(self, state: SupportGraphState) -> dict[str, Any]:
        """Handle ticket status queries like 'any update?'"""
        user_id = state.get("user_id", "")
        
        if not self._ticket_service:
            return {
                "final_message": "I don't have access to ticket information right now. Please try again later.",
            }
        
        # Get context for last_ticket_id
        context = await self.context_manager.get(user_id)
        
        response = await handle_ticket_status(
            user_id=user_id,
            ticket_service=self._ticket_service,
            last_ticket_id=context.last_ticket_id,
        )
        
        return {
            "response": response,
            "final_message": response.message,
        }

    async def _clarify_node(self, state: SupportGraphState) -> dict[str, Any]:
        """Ask for clarification."""
        resolver_decision = state.get("resolver_decision")
        user_id = state.get("user_id", "")
        
        # Increment attempts when asking for clarification
        await self.context_manager.increment_attempts(user_id)
        
        if resolver_decision and resolver_decision.prompts:
            prompt = resolver_decision.prompts[0]
            if prompt.key == "support.ask_reference":
                question = "Which transaction are you asking about?\n\nYou can:\n• Reply to the receipt message\n• Share the amount and recipient\n• Say \"my last transfer\""
            elif prompt.key == "support.negotiate":
                question = resolver_decision.negotiation.message if resolver_decision.negotiation else "I need more information to help you."
            else:
                question = "Could you provide more details?"
        else:
            question = state.get("clarification_question", "Which transaction are you asking about?")
        
        return {
            "final_message": question,
            "needs_clarification": True,
        }

    async def _no_transaction_node(self, state: SupportGraphState) -> dict[str, Any]:
        """Handle case where no transaction was found."""
        user_id = state.get("user_id", "")
        context = await self.context_manager.get(user_id)
        
        # Check if we should create ticket after too many attempts
        if context.attempts >= 3:
            if self._ticket_service:
                intent = state.get("intent")
                response = await handle_escalation(
                    user_id=user_id,
                    intent=intent.value if intent else "general_tx_issue",
                    ticket_service=self._ticket_service,
                    reason="tx_not_found_max_attempts",
                )
                return {
                    "final_message": response.message,
                    "ticket_code": response.escalation.context.get("ticket_code") if response.escalation else None,
                }
        
        return {
            "final_message": (
                "I couldn't find a recent transaction matching your query.\n\n"
                "Could you provide more details, or reply to the receipt message?"
            ),
        }

    async def _exit_not_support_node(self, state: SupportGraphState) -> dict[str, Any]:
        """Exit when message is not a support intent."""
        return {
            "final_message": None,
        }

    async def run(
        self,
        phone_number: str,
        message: str,
        user_id: str,
        message_id: str = "",
        quoted_message_id: str | None = None,
        transaction: dict[str, Any] | None = None,
    ) -> str | None:
        """
        Run the support flow.

        Returns:
            Response message, or None if not a support query.
        """
        await self._ensure_checkpointer()

        if self._graph is None:
            raise RuntimeError("Graph not compiled")

        config = self._get_config(phone_number)

        initial_state: SupportGraphState = {
            "phone_number": phone_number,
            "message": message,
            "message_id": message_id,
            "user_id": user_id,
            "quoted_message_id": quoted_message_id,
            "transaction": transaction,
        }

        try:
            final_state = await self._graph.ainvoke(initial_state, config)
            return final_state.get("final_message")

        except Exception as e:
            logger.error("support_flow_error", error=str(e))
            return "Something went wrong. Please try again or ask to speak with support."

    def is_support_query(self, classification_result: dict) -> bool:
        """Check if a classification result indicates a support query."""
        return classification_result.get("intent") is not None
