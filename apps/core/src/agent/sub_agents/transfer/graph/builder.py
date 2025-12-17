"""Graph construction for transfer flow."""

from langgraph.graph import StateGraph, END

from apps.core.src.agent.sub_agents.transfer.extractor import TransferEntityExtractor
from apps.core.src.agent.tools.beneficiary.matcher import BeneficiaryMatcher
from apps.core.src.agent.tools.validation.service import AsyncValidationService
from apps.core.src.agent.sub_agents.transfer.nodes import (
    extract_entities,
    load_user_context,
    validate_amount,
    select_source_account,
    find_beneficiary,
    validate_parallel,
    check_and_acknowledge_changes,
    prepare_confirmation,
    authorize_transaction,
    handle_cancellation,
)
from apps.core.src.agent.sub_agents.transfer.state import TransferState
from shared.cache.user_data import UserDataCache
from shared.cache.bank_cache import BankCacheService
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.repositories.account_repository import AccountRepository
from shared.clients.whatsapp_client import WhatsAppClient
from shared.cache.redis_client import Redis
from shared.queue.redis_queue import RedisQueue
from apps.core.src.agent.tools.authorization.service import AuthorizationService

from .routing import route_by_state, route_after_extract


def build_graph(
    extractor: TransferEntityExtractor,
    user_cache: UserDataCache,
    account_repo: AccountRepository,
    beneficiary_repo: BeneficiaryRepository,
    matcher: BeneficiaryMatcher,
    validation_service: AsyncValidationService | None,
    bank_cache: BankCacheService,
    payment_provider,
    whatsapp_client: WhatsAppClient,
    redis_client: Redis,
    queue: RedisQueue,
) -> StateGraph:
    """Build the LangGraph workflow."""
    workflow = StateGraph(TransferState)
    authorization_service = AuthorizationService(redis_client=redis_client)

    async def extract_node(state: TransferState) -> TransferState:
        return await extract_entities(state, extractor)

    async def load_context_node(state: TransferState) -> TransferState:
        return await load_user_context(
            state, user_cache, account_repo, beneficiary_repo
        )

    async def find_beneficiary_node(state: TransferState) -> TransferState:
        return await find_beneficiary(state, matcher)

    async def fetch_banks_func():
        """Helper function to fetch banks from payment provider."""
        if not payment_provider or not hasattr(payment_provider, 'fetch_banks'):
            return {"success": False, "banks": [], "error": "Provider does not support fetch_banks"}
        try:
            result = await payment_provider.fetch_banks(country="NG")
            return result
        except Exception as e:
            return {"success": False, "banks": [], "error": str(e)}

    async def validate_parallel_node(state: TransferState) -> TransferState:
        if validation_service:
            return await validate_parallel(
                state,
                validation_service,
                bank_cache,
                fetch_banks_func,
                whatsapp_client,
            )
        return state

    async def check_changes_node(state: TransferState) -> TransferState:
        return await check_and_acknowledge_changes(state, bank_cache)

    async def confirm_node(state: TransferState) -> TransferState:
        return await prepare_confirmation(
            state, whatsapp_client, redis_client
        )

    async def authorize_node(state: TransferState) -> TransferState:
        return await authorize_transaction(
            state, redis_client, queue, authorization_service
        )


    async def cancellation_node(state: TransferState) -> TransferState:
        return await handle_cancellation(state, redis_client)

    # Add nodes
    workflow.add_node("extract", extract_node)
    workflow.add_node("load_context", load_context_node)
    workflow.add_node("validate_amount", validate_amount)
    workflow.add_node("select_account", select_source_account)
    workflow.add_node("find_beneficiary", find_beneficiary_node)
    workflow.add_node("validate_parallel", validate_parallel_node)
    workflow.add_node("check_changes", check_changes_node)
    workflow.add_node("confirm", confirm_node)
    workflow.add_node("authorize", authorize_node)
    workflow.add_node("cancel", cancellation_node)

    workflow.set_entry_point("extract")

    workflow.add_conditional_edges(
        "extract",
        route_after_extract,
        {
            "cancel": "cancel",
            "load_context": "load_context",
            "authorize": "authorize",
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
            "validate": "validate_parallel",
            "check_changes": "check_changes",
            "confirm": "confirm",
            "cancel": "cancel",
        }
    )

    workflow.add_conditional_edges(
        "check_changes",
        route_by_state,
        {
            "end": END, 
            "validate": "validate_parallel", 
            "confirm": "confirm", 
            "cancel": "cancel",
        }
    )

    workflow.add_edge("confirm", END)

    workflow.add_edge("authorize", END)
    workflow.add_edge("cancel", END)

    return workflow
