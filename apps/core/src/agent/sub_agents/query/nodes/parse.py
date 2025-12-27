"""Parse and control nodes for query flow."""

from typing import Any

from apps.core.src.agent.sub_agents.query.graph.state import QueryState
from apps.core.src.agent.sub_agents.query.parser import QueryParser
from apps.core.src.agent.sub_agents.query.validators import QueryValidator
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def parse_node(state: QueryState, parser: QueryParser) -> dict[str, Any]:
    """Parse user query into structured parameters."""
    message = state["message"]
    phone_number = state["phone_number"]

    try:
        params = await parser.parse(message)

        # Validate parsed parameters
        is_valid, error_msg = QueryValidator.validate(params, phone_number)
        if not is_valid:
            return {
                "flow_state": "error",
                "response": error_msg or "Invalid query parameters.",
            }

        return {
            "flow_state": "fetching",
            "query_type": params.get("query_type", "transaction_list"),
            "date_range": params.get("date_range", {}),
            "narration_filter": params.get("narration_filter"),
            "transaction_type": params.get("transaction_type", "both"),
            "limit": params.get("limit", 10),
            "current_page": 0,
            "page_size": params.get("limit", 10),
            "amount_check": params.get("amount_check"),
            "analysis_type": params.get("analysis_type", "immediate"),
            "item_name": params.get("item_name"),
            "projection_months": params.get("projection_months"),
        }
    except Exception as e:
        logger.error("parse_node_error", error=str(e))
        return {
            "flow_state": "error",
            "response": "I couldn't understand your query. Could you rephrase it?",
        }


async def paginate_node(state: QueryState) -> dict[str, Any]:
    """Handle pagination - load next page."""
    current_page = state.get("current_page", 0)

    return {
        "flow_state": "aggregating",
        "current_page": current_page + 1,
    }


async def refine_node(state: QueryState, parser: QueryParser) -> dict[str, Any]:
    """Apply filter refinement to existing results."""
    new_filter = state.get("new_filter")

    if new_filter:
        return {
            "flow_state": "fetching",
            "narration_filter": new_filter,
            "current_page": 0,
        }

    return {"flow_state": "aggregating"}
