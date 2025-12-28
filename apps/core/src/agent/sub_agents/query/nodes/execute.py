"""Execute node for query flow - uses QueryExecutor."""

from typing import Any

from apps.core.src.agent.sub_agents.query.executor import QueryExecutor
from apps.core.src.agent.sub_agents.query.graph.state import QueryState
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def execute_node(state: QueryState, executor: QueryExecutor) -> dict[str, Any]:
    """Execute query using the QueryExecutor."""
    query = state.get("query")
    if not query:
        return {
            "flow_state": "error",
            "response": "No query to execute.",
        }

    account_id = state["account_id"]
    account_ids = state.get("account_ids", [account_id])

    try:
        result = await executor.execute(query, account_id, account_ids)

        return {
            "flow_state": "formatting",
            "query_result": result,
            "has_more": result.has_more,
            "session_active": True,
        }
    except Exception as e:
        logger.error("execute_node_error", error=str(e))
        return {
            "flow_state": "error",
            "response": "Something went wrong. Please try again.",
        }
