"""SupportFlowGraph - LangGraph for transaction support queries."""

from typing import Any

import redis.asyncio as redis
from langchain_core.runnables import Runnable, RunnableConfig
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.graph import END, StateGraph

from apps.core.src.agent.graphs.support.classifier import SupportClassifier
from apps.core.src.agent.graphs.support.graph.state import SupportGraphState
from apps.core.src.agent.graphs.support.handlers import (
    handle_escalation,
    handle_failure_reason,
    handle_fraud,
    handle_pending,
    handle_receipt_request,
    handle_retry,
    handle_reversal_status,
    handle_transfer_status,
    handle_wrong_debit,
)
from apps.core.src.agent.graphs.support.models import SupportIntent, SupportResponse
from apps.core.src.agent.graphs.support.resolver import TransactionResolver
from shared.config.settings import settings
from shared.repositories.actionable_message_repository import ActionableMessageRepository
from shared.repositories.transaction_repository import TransactionRepository
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def route_after_classify(state: SupportGraphState) -> str:
    """Route based on classification result."""
    if not state.get("intent"):
        return "exit_not_support"
    return "resolve"


def route_after_resolve(state: SupportGraphState) -> str:
    """Route based on transaction resolution."""
    if state.get("needs_clarification"):
        return "clarify"
    if not state.get("transaction") and state.get("intent") != SupportIntent.FRAUD_SUSPECTED:
        return "no_transaction"
    return "respond"


class SupportFlowGraph:
    """LangGraph-based support flow for transaction-bound issues with checkpointing."""

    def __init__(
        self,
        llm: Runnable,
        transaction_repo: TransactionRepository,
        actionable_message_repo: ActionableMessageRepository,
        redis_client: redis.Redis,
    ):
        self.llm = llm
        self.redis = redis_client
        self.classifier = SupportClassifier(llm)
        self.resolver = TransactionResolver(transaction_repo, actionable_message_repo)

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

        graph.add_node("classify", self._classify_node)
        graph.add_node("resolve", self._resolve_node)
        graph.add_node("respond", self._respond_node)
        graph.add_node("clarify", self._clarify_node)
        graph.add_node("no_transaction", self._no_transaction_node)
        graph.add_node("exit_not_support", self._exit_not_support_node)

        graph.set_entry_point("classify")

        graph.add_conditional_edges(
            "classify",
            route_after_classify,
            {
                "resolve": "resolve",
                "exit_not_support": "exit_not_support",
            },
        )

        graph.add_conditional_edges(
            "resolve",
            route_after_resolve,
            {
                "respond": "respond",
                "clarify": "clarify",
                "no_transaction": "no_transaction",
            },
        )

        graph.add_edge("respond", END)
        graph.add_edge("clarify", END)
        graph.add_edge("no_transaction", END)
        graph.add_edge("exit_not_support", END)

        return graph

    async def _classify_node(self, state: SupportGraphState) -> dict[str, Any]:
        """Classify the user message into a support intent."""
        message = state.get("message", "")
        result = await self.classifier.classify(message)

        return {
            "intent": result.intent,
            "classification": result,
        }

    async def _resolve_node(self, state: SupportGraphState) -> dict[str, Any]:
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
            return {
                "transaction": None,
                "resolution_method": method,
                "needs_clarification": False,
            }

        return {
            "transaction": None,
            "resolution_method": "ambiguous",
            "needs_clarification": True,
            "clarification_question": ("Which transaction are you asking about? Please share more details."),
        }

    async def _respond_node(self, state: SupportGraphState) -> dict[str, Any]:
        """Generate response based on intent and transaction."""
        intent = state.get("intent")
        transaction = state.get("transaction")

        response = await self._handle_intent(intent, transaction)

        return {
            "response": response,
            "escalation": response.escalation if response else None,
            "final_message": response.message if response else "Unable to process your request.",
        }

    async def _handle_intent(self, intent: SupportIntent | None, transaction: dict[str, Any] | None) -> SupportResponse:
        """Route to appropriate handler based on intent."""
        if intent == SupportIntent.TRANSFER_STATUS:
            return await handle_transfer_status(transaction)
        elif intent == SupportIntent.PENDING_TRANSFER:
            return await handle_pending(transaction)
        elif intent == SupportIntent.TRANSFER_FAILURE_REASON:
            return await handle_failure_reason(transaction)
        elif intent == SupportIntent.WRONG_DEBIT:
            return await handle_wrong_debit(transaction)
        elif intent == SupportIntent.REVERSAL_REFUND_STATUS:
            return await handle_reversal_status(transaction)
        elif intent == SupportIntent.RETRY_TRANSFER:
            return await handle_retry(transaction)
        elif intent == SupportIntent.FRAUD_SUSPECTED:
            return await handle_fraud(transaction)
        elif intent == SupportIntent.RECEIPT_REQUEST:
            return await handle_receipt_request(transaction)
        elif intent == SupportIntent.SUPPORT_ESCALATION:
            return await handle_escalation(transaction)
        else:
            return SupportResponse(message="I couldn't understand your request.")

    async def _clarify_node(self, state: SupportGraphState) -> dict[str, Any]:
        """Ask for clarification about which transaction."""
        question = state.get("clarification_question", "Which transaction are you asking about?")
        return {
            "final_message": question,
            "needs_clarification": True,
        }

    async def _no_transaction_node(self, state: SupportGraphState) -> dict[str, Any]:
        """Handle case where no transaction was found."""
        return {
            "final_message": (
                "I couldn't find a recent transaction matching your query. Could you provide more details?"
            ),
        }

    async def _exit_not_support_node(self, state: SupportGraphState) -> dict[str, Any]:
        """Exit when message is not a support intent."""
        return {
            "final_message": None,  # Signal that this should route elsewhere
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

        Args:
            phone_number: User's phone number
            message: User's message
            user_id: User's ID
            message_id: Message ID
            quoted_message_id: ID of quoted message if any
            transaction: Pre-resolved transaction data (skips resolve step)

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
