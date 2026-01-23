"""Execution step for query pipeline."""

from typing import Any

from apps.core.src.agent.graphs.query.actions import handle_drill_down
from apps.core.src.agent.graphs.query.executor import QueryExecutor
from apps.core.src.agent.graphs.query.formatter import QueryFormatter
from apps.core.src.agent.graphs.query.models import QueryResult, QueryResultItem
from apps.core.src.agent.graphs.query.pipeline import QueryStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class ExecutionStep(QueryStep):
    """Executes the query and formats the response."""

    def __init__(self):
        # Executor is instantiated in run() using provider from context, 
        # or we could stick to stateless executor usage if possible.
        # QueryExecutor holds 'provider'.
        pass

    async def run(
        self, state: dict[str, Any], worker_context: Any = None
    ) -> TransactionResult:
        """Run execution logic."""
        flow_state = state.get("flow_state")
        if flow_state != "executing":
            # Pass through if not executing
            return TransactionResult(outcome=TransactionOutcome.OK, patch={})

        query = state.get("query")
        # Ensure query object is proper model if it was dict
        if isinstance(query, dict):
            from apps.core.src.agent.graphs.query.models import NormalizedQuery
            query = NormalizedQuery.model_validate(query)

        account_id = state.get("account_id")
        account_ids = state.get("account_ids")
        accounts_info = state.get("accounts")
        current_page = state.get("current_page", 0)
        page_size = state.get("page_size", 5)
        user_id = worker_context.user_id if worker_context else None

        # Handle drill down (selecting single item from cache)
        if "selected_item_index" in state and state.get("query_session"):
            return await handle_drill_down(state)

        if not query:
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error="Internal error: Missing query parameters."
            )

        executor = QueryExecutor(worker_context.banking_provider)

        result = await executor.execute(
            query=query,
            account_id=account_id,
            account_ids=account_ids,
            accounts_info=accounts_info,
            current_page=current_page,
            page_size=page_size,
            user_id=user_id,
        )
        
        formatted_response = QueryFormatter.format(
            result,
            current_page=current_page,
            show_expanded=state.get("show_expanded", False),
            has_more=result.has_more 
        )

        # Prepend resolver message
        if state.get("resolver_message"):
            formatted_response = f"_{state['resolver_message']}_\n\n{formatted_response}"

        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response=formatted_response,
            patch={
                "query_result": result,
                "session_active": True, 
                "flow_state": "complete",
                "last_successful_query": query,
            },
        )
