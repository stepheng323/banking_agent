"""LangGraph graph for transfer flow."""

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
    prepare_confirmation,
)
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


def route_by_state(state: TransferState) -> Literal["end", "collect_amount", "select_account", "collect_recipient", "validate", "confirm"]:
    """Route based on current flow state and missing data."""
    flow_state = state.get("flow_state")
    response = state.get("response", "")
    amount = state.get("amount")
    selected_account = state.get("selected_source_account")
    recipient_account = state.get("recipient_account")
    recipient_bank = state.get(
        "recipient_bank_code") or state.get("recipient_bank_name")

    print(f"DEBUG route_by_state: flow_state={flow_state}, response={bool(response)}, amount={amount}, selected_account={bool(selected_account)}, recipient_account={bool(recipient_account)}, recipient_bank={bool(recipient_bank)}")

    if response and flow_state in ("collecting_amount", "selecting_account", "collecting_recipient", "error", "confirming"):
        print(f"DEBUG route_by_state: Early exit due to response")
        return "end"

    if not amount:
        print(f"DEBUG route_by_state: Routing to collect_amount")
        return "collect_amount"

    if not selected_account:
        print(f"DEBUG route_by_state: Routing to select_account")
        return "select_account"

    if not recipient_account or not recipient_bank:
        print(f"DEBUG route_by_state: Routing to collect_recipient")
        return "collect_recipient"

    if flow_state == "validating":
        print(f"DEBUG route_by_state: Routing to confirm")
        return "confirm"

    if flow_state == "extracting":
        print(f"DEBUG route_by_state: Routing to validate")
        return "validate"

    print(f"DEBUG route_by_state: Default end")
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

        async def confirm_node(state: TransferState) -> TransferState:
            return await prepare_confirmation(
                state, self.whatsapp_client, self.redis_client
            )

        # Add nodes
        workflow.add_node("extract", extract_node)
        workflow.add_node("load_context", load_context_node)
        workflow.add_node("validate_amount", validate_amount)
        workflow.add_node("select_account", select_source_account)
        workflow.add_node("find_beneficiary", find_beneficiary_node)
        workflow.add_node("validate_parallel", validate_parallel_node)
        workflow.add_node("confirm", confirm_node)

        workflow.set_entry_point("extract")

        workflow.add_edge("extract", "load_context")
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
            }
        )

        workflow.add_conditional_edges(
            "find_beneficiary",
            route_by_state,
            {
                "end": END,
                "validate": "validate_parallel",
                "confirm": "confirm",
            }
        )

        workflow.add_conditional_edges(
            "validate_parallel",
            route_by_state,
            {
                "end": END,
                "confirm": "confirm",
            }
        )

        workflow.add_edge("confirm", END)

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
                print(
                    f"DEBUG graph.run: Loaded checkpoint, amount: {input_state.get('amount')}")
            else:
                # First turn - use initial state
                input_state = create_initial_state(
                    phone_number, message, message_id)
                print(
                    f"DEBUG graph.run: First turn, amount: {input_state.get('amount')}")
        except Exception as e:
            # If state retrieval fails, use initial state
            print(
                f"DEBUG graph.run: Could not load checkpoint: {e}, using initial state")
            input_state = create_initial_state(
                phone_number, message, message_id)

        print(f"DEBUG graph.run: Message: {message}")

        final_state = await self.graph.ainvoke(cast(TransferState, input_state), config)

        print(
            f"DEBUG graph.run: Final state amount: {final_state.get('amount')}")
        return final_state.get("response", "")
