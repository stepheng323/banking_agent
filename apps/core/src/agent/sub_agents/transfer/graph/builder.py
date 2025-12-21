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
    check_funding,
    plan_funding,
    confirm_funding,
    initiate_debits,
)
from apps.core.src.agent.sub_agents.transfer.state import TransferState
from shared.cache.user_data import UserDataCache
from shared.cache.bank_cache import BankCacheService
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.repositories.account_repository import AccountRepository
from shared.clients.whatsapp.client import WhatsAppClient
from shared.clients.abstractions import DirectDebitProvider
from shared.clients.factories import get_direct_debit_provider
from shared.cache.redis_client import Redis
from shared.queue.redis_queue import RedisQueue
from shared.services.auth import AuthorizationService

from .routing import route_by_state, route_after_extract, route_after_funding_check


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
    direct_debit_provider: DirectDebitProvider | None = None,
) -> StateGraph:
    """Build the LangGraph workflow."""
    workflow = StateGraph(TransferState)
    authorization_service = AuthorizationService(redis_client=redis_client)
    
    dd_provider = direct_debit_provider or get_direct_debit_provider()

    async def extract_node(state: TransferState) -> TransferState:
        return await extract_entities(state, extractor)

    async def load_context_node(state: TransferState) -> TransferState:
        return await load_user_context(
            state, user_cache, account_repo, beneficiary_repo
        )

    async def find_beneficiary_node(state: TransferState) -> TransferState:
        return await find_beneficiary(state, matcher)

    async def fetch_banks_func():
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

    async def check_funding_node(state: TransferState) -> TransferState:
        return await check_funding(state, dd_provider)

    async def plan_funding_node(state: TransferState) -> TransferState:
        return await plan_funding(state, dd_provider)

    async def confirm_funding_node(state: TransferState) -> TransferState:
        return await confirm_funding(state, whatsapp_client)

    async def initiate_debits_node(state: TransferState) -> TransferState:
        return await initiate_debits(state, dd_provider)

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
    workflow.add_node("check_funding", check_funding_node)
    workflow.add_node("plan_funding", plan_funding_node)
    workflow.add_node("confirm_funding", confirm_funding_node)
    workflow.add_node("initiate_debits", initiate_debits_node)
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

    # confirm → check_funding (for multi-account support)
    workflow.add_edge("confirm", "check_funding")

    # check_funding routes based on funding_required
    workflow.add_conditional_edges(
        "check_funding",
        route_after_funding_check,
        {
            "authorize": "authorize",
            "plan_funding": "plan_funding",
            "error": END,
        }
    )

    # plan_funding routes based on result
    workflow.add_conditional_edges(
        "plan_funding",
        route_after_funding_check,
        {
            "authorize": "authorize",
            "confirm_funding": "confirm_funding",
            "error": END,
        }
    )

    # confirm_funding waits for user response, then initiates debits
    workflow.add_edge("confirm_funding", "initiate_debits")

    # initiate_debits → authorize (after debits initiated)
    workflow.add_conditional_edges(
        "initiate_debits",
        route_after_funding_check,
        {
            "authorize": "authorize",
            "error": END,
        }
    )

    workflow.add_edge("authorize", END)
    workflow.add_edge("cancel", END)

    return workflow

