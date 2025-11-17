"""Graph builder for airtime purchase flow."""

from langgraph.graph import StateGraph, END

from apps.core.src.agent.airtime.extractor import AirtimeEntityExtractor
from apps.core.src.agent.airtime.state import AirtimeState
from apps.core.src.agent.airtime.nodes import (
    extract_entities,
    load_user_context,
    validate_amount,
    select_source_account,
    find_beneficiary,
    prepare_confirmation,
    handle_cancellation,
)

from apps.core.src.agent.services.beneficiary_matcher import BeneficiaryMatcher
from shared.cache.user_context_cache import UserContextCacheService
from shared.repositories import BeneficiaryRepository, AccountRepository
from shared.clients.whatsapp_client import WhatsAppClient
from shared.cache.redis_client import RedisClient

from .routing import route_by_state, route_after_extract


def build_graph(
    extractor: AirtimeEntityExtractor,
    user_cache: UserContextCacheService,
    account_repo: AccountRepository,
    beneficiary_repo: BeneficiaryRepository,
    matcher: BeneficiaryMatcher,
    whatsapp_client: WhatsAppClient,
    redis_client: RedisClient,
) -> StateGraph:
    """Build the airtime purchase flow graph."""
    workflow = StateGraph(AirtimeState)

    async def extract_node(state: AirtimeState) -> AirtimeState:
        return await extract_entities(state, extractor)

    async def load_context_node(state: AirtimeState) -> AirtimeState:
        return await load_user_context(
            state, user_cache, account_repo, beneficiary_repo
        )

    async def find_beneficiary_node(state: AirtimeState) -> AirtimeState:
        return await find_beneficiary(state, matcher)

    async def confirm_node(state: AirtimeState) -> AirtimeState:
        return await prepare_confirmation(
            state, whatsapp_client, redis_client
        )

    async def cancellation_node(state: AirtimeState) -> AirtimeState:
        return await handle_cancellation(
            state, redis_client
        )

    # Add nodes
    workflow.add_node("extract", extract_node)
    workflow.add_node("load_context", load_context_node)
    workflow.add_node("validate_amount", validate_amount)
    workflow.add_node("select_account", select_source_account)
    workflow.add_node("find_beneficiary", find_beneficiary_node)
    workflow.add_node("confirm", confirm_node)
    workflow.add_node("cancel", cancellation_node)

    workflow.set_entry_point("extract")

    workflow.add_conditional_edges(
        "extract",
        route_after_extract,
        {
            "cancel": "cancel",
            "__route__": "load_context",
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
            "collect_phone": "find_beneficiary",
            "validate": "validate_amount",
            "confirm": "confirm",
            "cancel": "cancel",
        }
    )

    workflow.add_conditional_edges(
        "select_account",   
        route_by_state,
        {
            "end": END,
            "collect_phone": "find_beneficiary",
            "validate": "validate_amount",
            "confirm": "confirm",
            "cancel": "cancel",
        }
    )

    workflow.add_conditional_edges(
        "find_beneficiary",
        route_by_state,
        {
            "end": END,
            "collect_phone": "find_beneficiary",
            "validate": "validate_amount",
            "confirm": "confirm",
            "cancel": "cancel",
        }
    )

    workflow.add_edge("confirm", END)

    workflow.add_edge("cancel", END)

    return workflow
