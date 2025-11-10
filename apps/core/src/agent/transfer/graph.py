"""LangGraph graph for transfer flow."""

import json
from typing import Literal, cast
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.transfer.state import TransferState
from apps.core.src.agent.transfer.nodes import (
    extract_entities,
    load_user_context,
    validate_amount,
    select_source_account,
    find_beneficiary,
    validate_parallel,
    check_and_acknowledge_changes,
    prepare_confirmation,
    handle_cancellation,
)
# Note: Cancellation detection is now handled in extract_entities node using LLM classification
from apps.core.src.agent.services.transfer_entity_extractor import TransferEntityExtractor
from apps.core.src.agent.services.beneficiary_matcher import BeneficiaryMatcher
from apps.core.src.agent.services.validation_service import AsyncValidationService
from shared.cache.user_context_cache import UserContextCacheService
from shared.cache.bank_cache import BankCacheService
from shared.repositories.account_repository import AccountRepository
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.clients.whatsapp_client import WhatsAppClient
from shared.clients.payment_provider_factory import PaymentProviderFactory
from shared.cache.redis_client import RedisClient


def create_initial_state(phone_number: str, message: str, message_id: str) -> TransferState:
    """Create initial state for transfer flow."""
    return {
        # User identification
        "phone_number": phone_number,
        "message": message,
        "message_id": message_id,
        # Flow state
        "active_flow": "transfer",
        "flow_state": "extracting",
        # Entities
        "amount": None,
        "recipient_name": None,
        "recipient_account": None,
        "recipient_bank_code": None,
        "recipient_bank_name": None,
        "source_account_id": None,
        "narration": None,
        "missing_fields": [],
        # User context
        "user_profile": None,
        "accounts": [],
        "beneficiaries": [],
        # Selected/resolved values
        "selected_source_account": None,
        "matched_beneficiary": None,
        "account_resolved": None,
        "balance_available": None,
        "validation_errors": [],
        # Response
        "response": "",
        "llm_reply": None,
        # Metadata
        "idempotency_key": None,
        "transfer_status": None,
    }


def route_by_state(state: TransferState) -> Literal["end", "collect_amount", "select_account", "collect_recipient", "validate", "check_changes", "confirm", "cancel"]:
    """Route based on current flow state and missing data."""
    flow_state = state.get("flow_state")
    response = state.get("response", "")
    amount = state.get("amount")
    selected_account = state.get("selected_source_account")
    recipient_account = state.get("recipient_account")
    recipient_bank = state.get(
        "recipient_bank_code") or state.get("recipient_bank_name")
    account_resolved = state.get("account_resolved")

    # Check if cancellation was detected but not yet handled
    # If flow_state is "cancelled" but response is empty, route to cancel node
    if flow_state == "cancelled" and not response:
        return "cancel"

    # If already cancelled and response is set, end to send it
    if flow_state == "cancelled" and response:
        return "end"

    # Priority: If account_resolved was cleared (recipient info changed), re-validate first
    # This takes priority over sending responses to ensure account is re-validated
    if flow_state == "validating" and not account_resolved:
        # Make sure we have recipient info to validate
        if recipient_account and recipient_bank:
            return "validate"
        # If no recipient info, go back to collecting
        return "collect_recipient"

    # If we have a response, end to send it (unless we need to re-validate)
    if response and flow_state in ("collecting_amount", "selecting_account", "collecting_recipient", "error", "confirming"):
        return "end"

    if not amount:
        return "collect_amount"

    if not selected_account:
        print(f"DEBUG route_by_state: No selected account, routing to select_account")
        return "select_account"

    if not recipient_account or not recipient_bank:
        return "collect_recipient"

    # After validation, check for changes before confirming
    if flow_state == "validating":
        # Only route to check_changes if changes haven't been acknowledged yet
        # This prevents routing loops and ensures we don't check changes multiple times
        change_acknowledged = state.get("_change_acknowledged", False)
        if not change_acknowledged and account_resolved:
            # Only check changes if account is resolved (otherwise we need to validate first)
            return "check_changes"
        # Changes already acknowledged or account needs validation, proceed to confirmation
        return "confirm"

    # If flow_state is "confirming", route to confirm node
    if flow_state == "confirming":
        return "confirm"

    if flow_state == "extracting":
        return "validate"

    return "end"


class TransferFlowGraph:
    """LangGraph-based transfer flow."""

    def __init__(
        self,
        user_cache: UserContextCacheService,
        beneficiary_repo: BeneficiaryRepository,
        account_repo: AccountRepository,
        whatsapp_client: WhatsAppClient,
        extractor: TransferEntityExtractor,
    ):
        self.user_cache = user_cache
        self.beneficiary_repo = beneficiary_repo
        self.account_repo = account_repo
        self.whatsapp_client = whatsapp_client
        self.extractor = extractor
        self.matcher = BeneficiaryMatcher()

        try:
            provider = PaymentProviderFactory.get_provider_for_service(
                "resolve_account")
        except Exception:
            provider = None
        self.validation_service = AsyncValidationService(
            provider) if provider else None

        self.redis_client = RedisClient.get_client()
        self.bank_cache = BankCacheService(redis_client=self.redis_client)
        self.payment_provider = provider  # Store for bank fetching

        self._graph_builder = self._build_graph()
        self.graph = None
        self._checkpointer_cm = None
        self._checkpointer = None
        self._checkpointer_setup = False

    def _build_graph(self) -> StateGraph:
        """Build the LangGraph workflow."""
        workflow = StateGraph(TransferState)

        async def extract_node(state: TransferState) -> TransferState:
            return await extract_entities(state, self.extractor)

        async def load_context_node(state: TransferState) -> TransferState:
            return await load_user_context(
                state, self.user_cache, self.account_repo, self.beneficiary_repo
            )

        async def find_beneficiary_node(state: TransferState) -> TransferState:
            return await find_beneficiary(state, self.matcher)

        async def fetch_banks_func():
            """Helper function to fetch banks from payment provider."""
            if not self.payment_provider or not hasattr(self.payment_provider, 'fetch_banks'):
                return {"success": False, "banks": [], "error": "Provider does not support fetch_banks"}
            try:
                result = await self.payment_provider.fetch_banks(country="NG")
                return result
            except Exception as e:
                return {"success": False, "banks": [], "error": str(e)}

        async def validate_parallel_node(state: TransferState) -> TransferState:
            if self.validation_service:
                return await validate_parallel(
                    state,
                    self.validation_service,
                    self.bank_cache,
                    fetch_banks_func
                )
            return state

        async def check_changes_node(state: TransferState) -> TransferState:
            return await check_and_acknowledge_changes(state, self.bank_cache)

        async def confirm_node(state: TransferState) -> TransferState:
            return await prepare_confirmation(
                state, self.whatsapp_client, self.redis_client
            )

        async def cancellation_node(state: TransferState) -> TransferState:
            return await handle_cancellation(state, self.redis_client)

        # Add nodes
        workflow.add_node("extract", extract_node)
        workflow.add_node("load_context", load_context_node)
        workflow.add_node("validate_amount", validate_amount)
        workflow.add_node("select_account", select_source_account)
        workflow.add_node("find_beneficiary", find_beneficiary_node)
        workflow.add_node("validate_parallel", validate_parallel_node)
        workflow.add_node("check_changes", check_changes_node)
        workflow.add_node("confirm", confirm_node)
        workflow.add_node("cancel", cancellation_node)

        workflow.set_entry_point("extract")

        # After extract, check if cancellation was detected and route directly to cancel node
        def route_after_extract(state: TransferState) -> str:
            flow_state = state.get("flow_state")
            response = state.get("response", "")
            if flow_state == "cancelled" and not response:
                print(f"🛑 Routing to cancel node after extract_entities")
                return "cancel"
            return "load_context"

        workflow.add_conditional_edges(
            "extract",
            route_after_extract,
            {
                "cancel": "cancel",
                "load_context": "load_context",
            }
        )
        workflow.add_edge("load_context", "validate_amount")

        workflow.add_conditional_edges(
            "validate_amount",
            route_by_state,
            {
                "end": END,
                "collect_amount": END,
                "select_account": "select_account",
                "collect_recipient": "find_beneficiary",
                "validate": "validate_parallel",
                "confirm": "confirm",
                "cancel": "cancel",
            }
        )

        workflow.add_conditional_edges(
            "select_account",
            route_by_state,
            {
                "end": END,
                "collect_recipient": "find_beneficiary",
                "validate": "validate_parallel",
                "confirm": "confirm",
                "cancel": "cancel",
            }
        )

        workflow.add_conditional_edges(
            "find_beneficiary",
            route_by_state,
            {
                "end": END,
                "validate": "validate_parallel",
                "confirm": "confirm",
                "cancel": "cancel",
            }
        )

        workflow.add_conditional_edges(
            "validate_parallel",
            route_by_state,
            {
                "end": END,
                "validate": "validate_parallel",  # Re-validate if account_resolved was cleared
                "check_changes": "check_changes",
                "confirm": "confirm",
                "cancel": "cancel",
            }
        )

        workflow.add_conditional_edges(
            "check_changes",
            route_by_state,
            {
                "end": END,  # If change message shown, end to send it
                "validate": "validate_parallel",  # If recipient changed, re-validate
                "confirm": "confirm",  # Otherwise proceed to confirmation
                "cancel": "cancel",
            }
        )

        workflow.add_edge("confirm", END)
        workflow.add_edge("cancel", END)

        return workflow

    async def _ensure_checkpointer(self):
        """Ensure checkpointer is initialized and graph is compiled."""
        if not self._checkpointer_setup:
            import os
            db_url = os.getenv("DATABASE_URL", "")
            if not db_url:
                raise ValueError("DATABASE_URL required for checkpointing")
            self._checkpointer_cm = AsyncPostgresSaver.from_conn_string(db_url)
            self._checkpointer = await self._checkpointer_cm.__aenter__()
            self._checkpointer_setup = True

        if self.graph is None:
            self.graph = self._build_graph().compile(checkpointer=self._checkpointer)

    async def _update_conversation_state(self, phone_number: str, state: TransferState) -> None:
        """Update conversation_state in Redis so orchestrator can detect active transactions."""
        try:
            redis_client = RedisClient.get_client()
            flow_state = state.get("flow_state")
            active_flow = state.get("active_flow")
            transfer_status = state.get("transfer_status")
            idem_key = state.get("idempotency_key")

            # Only save conversation_state if there's an active transaction
            # (not in initial/extracting state unless there's a pending transfer)
            should_save = False

            if flow_state == "cancelled":
                # Transaction cancelled - clear conversation_state
                key = f"user:{phone_number}:conversation_state"
                await redis_client.delete(key)
                return

            # Save if:
            # 1. Transfer is pending (waiting for PIN)
            # 2. Flow state indicates active transaction (not just extracting)
            # 3. Has idempotency key (transaction initiated)
            if (transfer_status == "pending" or
                (flow_state not in ("extracting", "error", None) and active_flow == "transfer") or
                    idem_key):
                should_save = True

            if should_save:
                conversation_state = {
                    "active_flow": active_flow,
                    "flow_state": flow_state,
                    "transfer_status": transfer_status,
                    "idempotency_key": idem_key,
                    "amount": state.get("amount"),
                    "recipient_account": state.get("recipient_account"),
                    "recipient_name": state.get("recipient_name"),
                    "recipient_bank_code": state.get("recipient_bank_code"),
                    "recipient_bank_name": state.get("recipient_bank_name"),
                }

                key = f"user:{phone_number}:conversation_state"
                # Save with 1 hour TTL (same as other conversation state)
                await redis_client.set(key, json.dumps(conversation_state), ex=3600)
                print(
                    f"✅ Updated conversation_state for {phone_number}: active_flow={active_flow}, flow_state={flow_state}, transfer_status={transfer_status}")
            else:
                # No active transaction - clear conversation_state if it exists
                key = f"user:{phone_number}:conversation_state"
                await redis_client.delete(key)
        except Exception as e:
            print(f"⚠️  Error updating conversation_state: {e}")
            # Don't fail if conversation state update fails

    async def run(self, phone_number: str, message: str, message_id: str) -> str:
        """Run the transfer flow graph."""
        await self._ensure_checkpointer()

        config: RunnableConfig = {
            "configurable": {
                "thread_id": f"transfer:{phone_number}",
            }
        }

        if self.graph is None:
            raise RuntimeError("Graph not compiled")

        # Load existing checkpoint state to preserve values from previous turns
        # LangGraph's ainvoke REPLACES state, so we must load checkpoint first
        # Use the graph's get_state method to get current checkpoint
        try:
            current_state = await self.graph.aget_state(config)
            if current_state and current_state.values:
                # Merge checkpoint state with new message fields
                input_state = dict(current_state.values)
                input_state.update({
                    "phone_number": phone_number,
                    "message": message,
                    "message_id": message_id,
                })
            else:
                input_state = create_initial_state(
                    phone_number, message, message_id)
        except Exception:
            input_state = create_initial_state(
                phone_number, message, message_id)

        final_state = await self.graph.ainvoke(cast(TransferState, input_state), config)

        # Update conversation_state in Redis so orchestrator can detect active transactions
        await self._update_conversation_state(phone_number, cast(TransferState, final_state))

        return final_state.get("response", "")
