"""Extraction step for query pipeline."""

from datetime import date
from typing import Any

from langchain_core.runnables import Runnable

from apps.core.src.agent.graphs.query.continuity import (
    ContinuationClassifier,
    apply_filter_delta,
    apply_time_delta,
)
from apps.core.src.agent.graphs.query.models import NormalizedQuery, QueryResultItem
from apps.core.src.agent.graphs.query.parser import QueryParser
from apps.core.src.agent.graphs.query.pipeline import QueryStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class ExtractionStep(QueryStep):
    """Extracts intent and parameters for query."""

    def __init__(self, llm: Runnable):
        self.parser = QueryParser(llm)
        self.classifier = ContinuationClassifier(llm)

    async def run(
        self, state: dict[str, Any], worker_context: Any = None
    ) -> TransactionResult:
        """Run extraction logic."""
        message = state.get("message", "")
        query_session = state.get("query_session")

        updates = {}
        
        # If we have an active session, check for continuity
        if query_session and query_session.get("session_active"):
            updates = await self._handle_continuation(state, query_session)
        else:
            updates = await self._parse_new_query(state)

        # Check if we need to stop for input (clarification/negotiation)
        if updates.get("transaction_outcome") == TransactionOutcome.NEEDS_INPUT:
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                response=updates.get("response"),
                patch=updates,
            )
        
        # Check if session ended
        if updates.get("flow_state") == "complete":
             return TransactionResult(
                outcome=TransactionOutcome.OK,
                response=updates.get("response", "Query completed."),
                patch=updates,
            )

        return TransactionResult(
            outcome=TransactionOutcome.OK,
            patch=updates,
        )

    async def _handle_continuation(
        self, state: dict[str, Any], session: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle possible continuation of previous query."""
        message = state.get("message", "")
        
        # Reconstruct items for context if available
        items = []
        if session.get("query_result") and session["query_result"].get("items"):
            raw_items = session["query_result"]["items"]
            items = [
                QueryResultItem.model_validate(i) if isinstance(i, dict) else i
                for i in raw_items
            ]

        cont_type, data = await self.classifier.classify(
            message,
            has_active_session=True,
            today=date.today().isoformat(),
            items=items,
        )

        logger.info("query_continuation_type", type=cont_type)

        # Default state updates
        updates = {
            "flow_state": "executing",
            "continuation_type": cont_type,
            # Merging session data is handled by the worker initiating the state, 
            # but we ensure critical keys are present or updated.
            # Ideally the 'state' passed in already has session data merged.
        }

        if cont_type == "show_more":
            # Just increment page, keep existing query
            updates["current_page"] = session.get("current_page", 0) + 1
            
        elif cont_type == "time_delta":
            original_query = session.get("query")
            if original_query:
                if isinstance(original_query, dict):
                    original_query = NormalizedQuery.model_validate(original_query)
                
                new_query = apply_time_delta(original_query, data["time_range"])
                updates["query"] = new_query
                updates["current_page"] = 0
                updates["show_expanded"] = False

        elif cont_type == "filter_delta":
            original_query = session.get("query")
            if original_query:
                if isinstance(original_query, dict):
                    original_query = NormalizedQuery.model_validate(original_query)
                
                new_query = apply_filter_delta(original_query, data["filters"])
                updates["query"] = new_query
                updates["current_page"] = 0
                updates["show_expanded"] = False

        elif cont_type == "expand":
            updates["show_expanded"] = True
            
        elif cont_type == "drill_down":
            drill_idx = data.get("drill_down_index", 0)
            if items and 0 <= drill_idx < len(items):
                updates["selected_item_index"] = drill_idx
                updates["drill_down_action"] = data.get("drill_down_action")

        elif cont_type == "recipient_drill_down":
            # Recipient drill down (filter by this recipient)
            recipient_name = data.get("recipient_name")
            if recipient_name:
                from apps.core.src.agent.graphs.query.models import Filters
                original_query = session.get("query")
                if original_query:
                     if isinstance(original_query, dict):
                        original_query = NormalizedQuery.model_validate(original_query)
                     
                     new_filters = Filters(merchant=[recipient_name])
                     new_query = apply_filter_delta(original_query, new_filters)
                     updates["query"] = new_query
                     updates["current_page"] = 0
                     updates["show_expanded"] = False

        elif cont_type == "unclear":
            # Treat as needs input? Or simply fallback to new query?
            # Usually fallback to new query check is safer.
            return await self._parse_new_query(state)

        elif cont_type == "new_query":
            return await self._parse_new_query(state)
        
        elif cont_type == "end_session":
            return {
                "transaction_outcome": TransactionOutcome.OK, # Or OK?
                "response": data.get("end_session_response", "Goodbye!"),
                "session_active": False,
                "flow_state": "complete"
            }

        return updates

    async def _parse_new_query(self, state: dict[str, Any]) -> dict[str, Any]:
        """Parse a fresh query."""
        message = state.get("message", "")
        
        extraction, resolver_msg = await self.parser.parse(message)
        
        # Check for resolver prompts (clarify/negotiate)
        if resolver_msg and (resolver_msg.startswith("clarify:") or resolver_msg.startswith("negotiate:")):
             clean_msg = resolver_msg.split(":", 1)[1]
             return {
                 "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
                 "response": clean_msg,
                 "flow_state": "parsing"
             }

        # Convert to NormalizedQuery
        query = self.parser.convert_to_normalized(extraction)
        
        return {
            "query": query,
            "resolver_message": resolver_msg,
            "flow_state": "executing",
            "current_page": 0,
            "session_active": True,
            "show_expanded": False,
        }
