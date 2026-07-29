"""Execution step for query pipeline."""

from typing import Any

from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome, TransactionResult
from banking.transactions.query.actions import handle_drill_down
from banking.transactions.query.contracts import SelectionPayload, SurfaceViewMode
from banking.transactions.query.conversation_focus import advance_focus
from banking.transactions.query.executor import QueryExecutor
from banking.transactions.query.models.conversation import QueryFocus
from banking.transactions.query.models.operations import QueryRequest
from banking.transactions.query.pipeline import QueryStep
from banking.transactions.query.presentation.formatter import QueryFormatter
from banking.transactions.query.presentation.surface_builder import build_surface_view
from banking.transactions.query.services.answers.strategy import select_answer_strategy
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class ExecutionStep(QueryStep):
    """Executes the query and formats the response."""

    def __init__(self) -> None:
        pass

    @staticmethod
    def _active_focus(raw: object) -> QueryFocus | None:
        if isinstance(raw, QueryFocus):
            return raw
        if isinstance(raw, dict):
            try:
                return QueryFocus.model_validate(raw)
            except Exception:
                return None
        return None

    @staticmethod
    def _selected_payload(raw: object) -> SelectionPayload | None:
        if isinstance(raw, SelectionPayload):
            return raw
        if isinstance(raw, dict):
            try:
                return SelectionPayload.model_validate(raw)
            except Exception:
                return None
        return None

    @staticmethod
    def _surface_type_name(result: Any) -> str | None:
        surface_view = getattr(result, "surface_view", None)
        if surface_view is None:
            return None
        mode_map = {
            SurfaceViewMode.DIRECT_ANSWER: "single_item",
            SurfaceViewMode.TRANSACTION_LIST: "list",
            SurfaceViewMode.GROUPED_SUMMARY: "summary",
            SurfaceViewMode.CLARIFICATION: "clarification",
        }
        return mode_map.get(surface_view.mode)

    @staticmethod
    def _build_interpretation(
        query_request: QueryRequest,
        *,
        continuation_type: str | None = None,
        continuation_delta_type: str | None = None,
    ) -> dict[str, Any]:
        """Build compact debug metadata from the authoritative operation."""
        return {
            "schema_version": query_request.schema_version,
            "operation": query_request.operation.model_dump(mode="json", exclude_none=True),
            "continuation_type": continuation_type,
            "continuation_delta_type": continuation_delta_type,
        }

    async def run(self, state: dict[str, Any], worker_context: Any = None) -> TransactionResult:
        """Run execution logic."""
        flow_state = state.get("flow_state")
        if flow_state != "executing":
            return TransactionResult(outcome=TransactionOutcome.OK, patch={})
        locale = LocaleManager.normalize(state.get("language")).value

        query_request = state.get("query_request")
        if isinstance(query_request, dict):
            query_request = QueryRequest.model_validate(query_request)

        account_id = state.get("account_id")
        account_ids_raw = state.get("account_ids")
        account_ids: list[str] = (
            [str(acc_id) for acc_id in account_ids_raw] if isinstance(account_ids_raw, list) else []
        )
        accounts_raw = state.get("accounts")
        accounts_info: list[dict[str, Any]] = (
            [acc for acc in accounts_raw if isinstance(acc, dict)] if isinstance(accounts_raw, list) else []
        )
        current_page = state.get("current_page", 0)
        page_size = state.get("page_size", 5)
        if not account_id and accounts_info:
            # Fallback to first available account
            first_acc = accounts_info[0]
            account_id = first_acc.get("account_id") or first_acc.get("mono_account_id")

        if not account_ids and accounts_info:
            # Default to all accounts if not specified
            resolved_account_ids: list[str] = []
            for acc in accounts_info:
                account_value = acc.get("account_id") or acc.get("mono_account_id")
                if account_value:
                    resolved_account_ids.append(str(account_value))
            account_ids = resolved_account_ids
        account_ids = [str(acc_id) for acc_id in account_ids]

        user_id = worker_context.user_id if worker_context else None
        query_session_raw = state.get("query_session")
        query_session: dict[str, Any] = query_session_raw if isinstance(query_session_raw, dict) else {}

        session_cache = {
            "cached_transactions": query_session.get("cached_transactions"),
            "cache_fetched_at": query_session.get("cache_fetched_at"),
            "cache_fingerprint": query_session.get("cache_fingerprint"),
            "cache_scope_fingerprint": query_session.get("cache_scope_fingerprint"),
            "cache_window_start": query_session.get("cache_window_start"),
            "cache_window_end": query_session.get("cache_window_end"),
        }

        if state.get("query_session") and (
            "selected_item_index" in state
            or state.get("selected_payload") is not None
            or state.get("selected_item_id") is not None
        ):
            return await handle_drill_down(state)

        if not isinstance(query_request, QueryRequest) or not account_id:
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=render_message("query.error.missing_params", locale),
            )

        executor = QueryExecutor(worker_context.banking_provider)
        resolved_account_id = str(account_id)

        result = await executor.execute(
            query=query_request,
            account_id=resolved_account_id,
            account_ids=account_ids,
            accounts_info=accounts_info,
            current_page=current_page,
            page_size=page_size,
            user_id=user_id,
            language=locale,
            continuation_type=state.get("continuation_type"),
            continuation_delta_type=state.get("continuation_delta_type"),
            session_cache=session_cache,
            trace_context={
                "turn_id": state.get("turn_id"),
                "inbound_message_id": state.get("inbound_message_id"),
            },
        )
        if result.query_request is None:
            result.query_request = query_request
        result.interpretation = self._build_interpretation(
            query_request,
            continuation_type=state.get("continuation_type"),
            continuation_delta_type=state.get("continuation_delta_type"),
        )
        result = select_answer_strategy(result, locale=locale)
        result.surface_view = build_surface_view(result)
        previous_focus = self._active_focus(state.get("active_focus"))
        focus = advance_focus(
            request=query_request,
            previous=previous_focus,
            continuation_type=state.get("continuation_type"),
            selected_payload=self._selected_payload(state.get("selected_payload")),
            source_frame_id=state.get("repair_source_frame_id") or state.get("selected_frame_id"),
            turn_id=state.get("turn_id"),
        )
        result.conversation_focus = focus
        logger.info(
            "query_focus_advanced",
            previous_source=previous_focus.source if previous_focus is not None else None,
            source=focus.source,
            subject=focus.subject,
            has_selected_entity=focus.selected_payload is not None,
            display_only_preserved=(
                previous_focus is not None
                and focus.source == previous_focus.source
                and focus.frame_id == previous_focus.frame_id
            ),
        )

        formatted_response = QueryFormatter.format(
            result,
            current_page=current_page,
            show_expanded=state.get("show_expanded", False),
            has_more=result.has_more,
            locale=locale,
        )

        if state.get("resolver_message"):
            formatted_response = f"_{state['resolver_message']}_\n\n{formatted_response}"

        logger.info(
            "query_execution_response_summary",
            resolver_message_attached=bool(state.get("resolver_message")),
            surface_type=self._surface_type_name(result),
            continuation_type=state.get("continuation_type"),
        )

        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response=formatted_response,
            patch={
                "query_result": result,
                "query_request": query_request,
                "active_focus": focus,
                "resolver_message": None,
                "session_active": True,
                "flow_state": "complete",
                "current_page": current_page,
                "page_size": page_size,
                "cached_transactions": result.cached_transactions,
                "cache_fetched_at": result.cache_fetched_at,
                "cache_fingerprint": result.cache_fingerprint,
                "cache_scope_fingerprint": result.cache_scope_fingerprint,
                "cache_window_start": result.cache_window_start,
                "cache_window_end": result.cache_window_end,
            },
        )
