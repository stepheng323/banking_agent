"""Parse and control nodes for query flow."""

from typing import Any

from apps.core.src.agent.sub_agents.query.continuity import (
    ContinuationClassifier,
    ContinuationType,
    apply_filter_delta,
    apply_time_delta,
)
from apps.core.src.agent.sub_agents.query.graph.state import QueryState
from apps.core.src.agent.sub_agents.query.parser import QueryParser
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def parse_node(state: QueryState, parser: QueryParser) -> dict[str, Any]:
    """Parse user query into NormalizedQuery."""
    message = state["message"]
    message_id = state.get("message_id", "")

    try:
        query, clarification = await parser.parse_with_validation(message, message_id)

        if clarification:
            return {
                "flow_state": "clarification_needed",
                "clarification_message": clarification,
                "response": clarification,
            }

        return {
            "flow_state": "fetching",
            "query": query,
            "current_page": 0,
            "page_size": query.aggregation.limit if query.aggregation else 10,
            "session_active": True,
        }
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

    try:
        cont_type, data = await classifier.classify(message, True, today)

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

        elif cont_type == ContinuationType.DRILL_DOWN:
            return {
                "continuation_type": "drill_down",
                "flow_state": "formatting",
                "drill_down_ref": data.get("reference"),
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
