"""Extraction step for query pipeline."""

from datetime import date
from typing import Any

from langchain_core.runnables import Runnable

from apps.core.src.agent.graphs.query.models import NormalizedQuery, QueryResultItem, ResolverOutcome, SurfaceType
from apps.core.src.agent.graphs.query.pipeline import QueryStep
from apps.core.src.agent.graphs.query.services.continuity import (
    ContinuationClassifier,
    apply_filter_delta,
    apply_time_delta,
)
from apps.core.src.agent.graphs.query.services.parser import QueryParser
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class ExtractionStep(QueryStep):
    """Extracts intent and parameters for query."""

    def __init__(self, llm: Runnable):
        self.parser = QueryParser(llm)
        self.classifier = ContinuationClassifier(llm)

    async def run(self, state: dict[str, Any], worker_context: Any = None) -> TransactionResult:
        """Run extraction logic."""
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

    async def _handle_continuation(self, state: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
        """Handle possible continuation of previous query."""
        message = state.get("message", "")

        # Reconstruct items for context if available
        items = []
        possible_result = session.get("query_result")

        if possible_result:
            raw_items = []
            if isinstance(possible_result, dict):
                raw_items = possible_result.get("items", [])
            else:
                raw_items = getattr(possible_result, "items", [])

            if raw_items:
                items = [QueryResultItem.model_validate(i) if isinstance(i, dict) else i for i in raw_items]

        cont_type, data = await self.classifier.classify(
            message,
            has_active_session=True,
            today=date.today().isoformat(),
            items=items,
            surface=session.get("surface"),
        )

        logger.info(
            "query_continuation_type",
            type=cont_type,
            confidence=data.get("confidence"),
            reason=data.get("reason"),
            override=data.get("is_new_query_override"),
        )

        # LLM override for new query or low-confidence classifications.
        if data.get("is_new_query_override"):
            return await self._parse_new_query(state)

        # Trust the LLM classification unless it explicitly signals a new-query override.

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

            # Special handling for BREAKDOWN surface: Drill down means filter by category
            surface = session.get("surface")
            if surface and surface.type == SurfaceType.BREAKDOWN:
                if items and 0 <= drill_idx < len(items):
                    selected_item = items[drill_idx]
                    category_name = selected_item.description  # Description holds the category name (e.g., "Food")

                    # Convert to filter_delta
                    from apps.core.src.agent.graphs.query.models import Filters

                    original_query = session.get("query")
                    if original_query:
                        if isinstance(original_query, dict):
                            original_query = NormalizedQuery.model_validate(original_query)

                        # Apply category filter
                        # Normalize category name (lowercase, handle 'Other' if needed)
                        cat_filter = category_name.lower()

                        logger.info(
                            "breakdown_drill_down_debug",
                            original_description=category_name,
                            applied_filter=cat_filter,
                            item_index=drill_idx,
                        )

                        new_filters = Filters(category=[cat_filter])
                        new_query = apply_filter_delta(original_query, new_filters)

                        # Reset aggregation to None (list view) or keep it?
                        # If drilling down, we usually want to see the transactions (List), not a sub-breakdown.
                        # Setting aggregation to None will switch to Transaction List.
                        new_query.aggregation = None

                        updates["query"] = new_query
                        updates["current_page"] = 0
                        updates["show_expanded"] = False

            # Default behavior for LIST surface (Item Detail)
            elif items and 0 <= drill_idx < len(items):
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
                "transaction_outcome": TransactionOutcome.OK,  # Or OK?
                "response": data.get("end_session_response", "Goodbye!"),
                "session_active": False,
                "flow_state": "complete",
            }

        return updates

    async def _parse_new_query(self, state: dict[str, Any]) -> dict[str, Any]:
        """Parse a fresh query."""
        message = state.get("message", "")
        today = state.get("today", date.today())

        result = await self.parser.parse(message, today=today)

        # Check for resolver outcomes
        if result.outcome == ResolverOutcome.NEEDS_INPUT:
            return {
                "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
                "response": result.resolver_message or "Could you clarify?",
                "flow_state": "parsing",
            }

        # Combine notices with resolver message
        resolver_msg_parts = []
        if result.resolver_message:
            resolver_msg_parts.append(result.resolver_message)

        if result.notices:
            resolver_msg_parts.extend(result.notices)

        resolver_msg = "\n".join(resolver_msg_parts) if resolver_msg_parts else None

        # Convert to NormalizedQuery
        query = self.parser.convert_to_normalized(result.extraction, today=today)

        return {
            "query": query,
            "resolver_message": resolver_msg,
            "flow_state": "executing",
            "current_page": 0,
            "session_active": True,
            "show_expanded": False,
        }
