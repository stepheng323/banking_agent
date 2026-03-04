"""Extraction step for query pipeline."""

from datetime import date
from typing import Any

from langchain_core.runnables import Runnable

from apps.core.src.agent.graphs.query.models import (
    NormalizedQuery,
    QueryResultItem,
    ResolverOutcome,
    SurfaceType,
    TimeReference,
)
from apps.core.src.agent.graphs.query.pipeline import QueryStep
from apps.core.src.agent.graphs.query.services.continuity import (
    ContinuationClassifier,
    apply_filter_delta,
    apply_time_delta,
)
from apps.core.src.agent.graphs.query.services.parser import QueryParser
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.i18n import LocaleManager, render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class ExtractionStep(QueryStep):
    """Extracts intent and parameters for query."""

    _LOW_CONFIDENCE_THRESHOLD = 0.45

    def __init__(self, llm: Runnable):
        self.parser = QueryParser(llm)
        self.classifier = ContinuationClassifier(llm)

    async def run(self, state: dict[str, Any], worker_context: Any = None) -> TransactionResult:
        """Run extraction logic."""
        query_session = state.get("query_session")
        locale = LocaleManager.normalize(state.get("language")).value

        updates: dict[str, Any] = {}
        force_new_query = bool(state.get("force_new_query"))

        # If we have an active session, check for continuity
        if force_new_query:
            updates = await self._parse_new_query(state)
        elif query_session and query_session.get("session_active"):
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
                response=updates.get("response", render_message("query.session.completed", locale)),
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
            language=LocaleManager.normalize(state.get("language")).value,
        )

        logger.info(
            "query_continuation_type",
            type=cont_type,
            confidence=data.get("confidence"),
            reason=data.get("reason"),
            override=data.get("is_new_query_override"),
        )

        # LLM override for new query or low-confidence classifications.
        if data.get("is_new_query_override") or data.get("restates_query"):
            return await self._parse_new_query(state)

        if self._should_force_new_query(message, cont_type, data):
            return await self._parse_new_query(state)

        # Trust the LLM classification unless it explicitly signals a new-query override.

        # Default state updates
        updates: dict[str, Any] = {
            "flow_state": "executing",
            "continuation_type": cont_type,
            "continuation_delta_type": data.get("delta_type"),
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
                if "result_limit" in data:
                    new_query.result_limit = data["result_limit"]
                if "result_reference" in data:
                    new_query.result_reference = data["result_reference"]
                updates["query"] = new_query
                updates["current_page"] = 0
                updates["show_expanded"] = False

        elif cont_type == "filter_delta":
            original_query = session.get("query")
            if original_query:
                if isinstance(original_query, dict):
                    original_query = NormalizedQuery.model_validate(original_query)

                delta_type = data.get("delta_type")
                allow_limit = delta_type in (None, "limit", "reference")
                allow_reference = delta_type in (None, "reference", "limit")

                new_query = apply_filter_delta(original_query, data["filters"]) if "filters" in data else original_query

                if "result_limit" in data and allow_limit:
                    new_query.result_limit = data["result_limit"]
                if "result_reference" in data and allow_reference:
                    new_query.result_reference = data["result_reference"]
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

        elif cont_type == "new_query" or cont_type == "aggregate":
            return await self._parse_new_query(state)

        elif cont_type == "end_session":
            locale = LocaleManager.normalize(state.get("language")).value
            return {
                "transaction_outcome": TransactionOutcome.OK,  # Or OK?
                "response": data.get("end_session_response", render_message("query.session.goodbye", locale)),
                "session_active": False,
                "flow_state": "complete",
            }

        return updates

    async def _parse_new_query(self, state: dict[str, Any]) -> dict[str, Any]:
        """Parse a fresh query."""
        message = state.get("message", "")
        today = state.get("today", date.today())
        language = LocaleManager.normalize(state.get("language")).value

        result = await self.parser.parse(message, today=today, language=language)

        # Check for resolver outcomes
        if result.outcome == ResolverOutcome.NEEDS_INPUT:
            clarify_fallback = render_message("query.clarify.default", language)
            return {
                "transaction_outcome": TransactionOutcome.NEEDS_INPUT,
                "response": result.resolver_message or clarify_fallback,
                "flow_state": "parsing",
            }

        # Combine notices with resolver message
        resolver_msg_parts = []
        if result.resolver_message:
            resolver_msg_parts.append(result.resolver_message)

        if result.notices:
            resolver_msg_parts.extend(result.notices)

        resolver_msg = "\n".join(resolver_msg_parts) if resolver_msg_parts else None

        if result.extraction is None:
            return {
                "transaction_outcome": TransactionOutcome.FAILED,
                "response": render_message("query.error.general", language),
                "flow_state": "parsing",
            }

        # Convert to NormalizedQuery
        query = self.parser.convert_to_normalized(result.extraction, today=today)

        # If this is a fresh parse with unspecified time, inherit prior active-session window.
        query_session = state.get("query_session")
        if (
            isinstance(query_session, dict)
            and query_session.get("session_active")
            and result.extraction.time_range.reference_type == TimeReference.UNSPECIFIED
        ):
            previous_query = query_session.get("query")
            if previous_query:
                try:
                    if isinstance(previous_query, dict):
                        previous_query = NormalizedQuery.model_validate(previous_query)
                    if isinstance(previous_query, NormalizedQuery) and previous_query.time_range:
                        old_start = query.time_range.start.isoformat() if query.time_range else None
                        old_end = query.time_range.end.isoformat() if query.time_range else None
                        query.time_range = previous_query.time_range.model_copy(deep=True)
                        logger.info(
                            "query_time_range_inherited_from_session",
                            previous_start=query.time_range.start.isoformat(),
                            previous_end=query.time_range.end.isoformat(),
                            replaced_start=old_start,
                            replaced_end=old_end,
                        )
                except Exception as exc:
                    logger.warning("query_time_range_inheritance_failed", error=str(exc))

        return {
            "query": query,
            "resolver_message": resolver_msg,
            "flow_state": "executing",
            "current_page": 0,
            "session_active": True,
            "show_expanded": False,
        }

    def _should_force_new_query(self, message: str, cont_type: str, data: dict[str, Any]) -> bool:
        """Rule-based fallback when follow-up classification is low confidence."""
        confidence = data.get("confidence")
        if confidence is None or confidence >= self._LOW_CONFIDENCE_THRESHOLD:
            return False

        if cont_type in ("show_more", "end_session"):
            return False

        word_count = len(message.split())
        return word_count >= 3
