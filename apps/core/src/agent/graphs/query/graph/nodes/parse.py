"""Parse and control nodes for query flow."""

from typing import Any

from apps.core.src.agent.graphs.query.continuity import (
    ContinuationClassifier,
    ContinuationType,
    apply_filter_delta,
    apply_time_delta,
)
from apps.core.src.agent.graphs.query.graph.state import QueryState
from apps.core.src.agent.graphs.query.parser import QueryParser
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def parse_node(state: QueryState, parser: QueryParser) -> dict[str, Any]:
    """Parse query using extraction with resolver integration."""
    message = state["message"]
    message_id = state.get("message_id", "")

    try:
        extraction, resolver_msg = await parser.parse(message, message_id)
        
        if resolver_msg:
            if resolver_msg.startswith("clarify:"):
                return {
                    "flow_state": "clarification_needed",
                    "response": resolver_msg.replace("clarify:", ""),
                }
            if resolver_msg.startswith("negotiate:"):
                return {
                    "flow_state": "clarification_needed",
                    "response": resolver_msg.replace("negotiate:", ""),
                    "session_active": True,
                }
        
        # Convert extraction to NormalizedQuery for handlers
        query = parser.convert_to_normalized(extraction)
        
        result = {
            "flow_state": "fetching",
            "query": query,
            "current_page": 0,
            "page_size": query.aggregation.limit if query.aggregation else 5,
            "session_active": True,
        }
        
        # Include clamping message for format node
        if resolver_msg and not resolver_msg.startswith(("clarify:", "negotiate:")):
            result["resolver_message"] = resolver_msg
        
        return result
        
    except Exception as e:
        logger.error("parse_node_error", error=str(e))
        return {
            "flow_state": "error",
            "response": "I couldn't understand your query. Could you rephrase it?",
        }


async def classify_continuation_node(
    state: QueryState,
    classifier: ContinuationClassifier,
    today: str,
) -> dict[str, Any]:
    """Classify continuation type using LLM."""
    message = state["message"]
    query_result = state.get("query_result")
    items = query_result.items if query_result else None

    try:
        cont_type, data = await classifier.classify(message, True, today, items)

        if cont_type == ContinuationType.SHOW_MORE:
            return {
                "continuation_type": "show_more",
                "flow_state": "paginating",
            }

        elif cont_type == ContinuationType.TIME_DELTA:
            query = state.get("query")
            if query and data.get("time_range"):
                updated_query = apply_time_delta(query, data["time_range"])
                return {
                    "continuation_type": "time_delta",
                    "query": updated_query,
                    "flow_state": "fetching",
                    "current_page": 0,
                }
            return {"flow_state": "parsing"}

        elif cont_type == ContinuationType.FILTER_DELTA:
            query = state.get("query")
            if query and data.get("filters"):
                updated_query = apply_filter_delta(query, data["filters"])
                return {
                    "continuation_type": "filter_delta",
                    "query": updated_query,
                    "flow_state": "fetching",
                    "current_page": 0,
                }
            return {"flow_state": "parsing"}

        elif cont_type == ContinuationType.EXPAND:
            # User wants to see underlying transactions from analytics summary
            return {
                "continuation_type": "expand",
                "flow_state": "formatting",
            }

        elif cont_type == ContinuationType.DRILL_DOWN:
            return {
                "continuation_type": "drill_down",
                "flow_state": "formatting",
                "drill_down_index": data.get("drill_down_index", 0),
                "drill_down_action": data.get("drill_down_action", "view_details"),
            }

        elif cont_type == ContinuationType.RECIPIENT_DRILL_DOWN:
            return {
                "continuation_type": "recipient_drill_down",
                "flow_state": "formatting",
                "recipient_name": data.get("recipient_name", ""),
            }

        elif cont_type == ContinuationType.UNCLEAR:
            return {
                "continuation_type": "unclear",
                "flow_state": "clarification_needed",
            }

        elif cont_type == ContinuationType.END_SESSION:
            return {
                "continuation_type": "end_session",
                "flow_state": "complete",
                "response": data.get("end_session_response", "You're welcome! 😊"),
                "session_active": False,
            }

        else:
            return {"flow_state": "parsing"}

    except Exception as e:
        logger.error("classify_continuation_error", error=str(e))
        return {"flow_state": "parsing"}


async def paginate_node(state: QueryState) -> dict[str, Any]:
    """Handle pagination - load next page."""
    current_page = state.get("current_page", 0)

    return {
        "flow_state": "aggregating",
        "current_page": current_page + 1,
    }
