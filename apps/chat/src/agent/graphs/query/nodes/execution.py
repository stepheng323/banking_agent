"""Execution step for query pipeline."""

from typing import Any

from apps.chat.src.agent.graphs.query.actions import handle_drill_down
from apps.chat.src.agent.graphs.query.executor import QueryExecutor
from apps.chat.src.agent.graphs.query.models import QueryExecutionContract
from apps.chat.src.agent.graphs.query.pipeline import QueryStep
from apps.chat.src.agent.graphs.query.services.answer_strategy import select_answer_strategy
from apps.chat.src.agent.graphs.query.services.contracts import build_surface_view
from apps.chat.src.agent.graphs.query.services.formatter import QueryFormatter
from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from apps.chat.src.agent.shared.query_contracts import SurfaceViewMode
from shared.i18n import LocaleManager, render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class ExecutionStep(QueryStep):
    """Executes the query and formats the response."""

    def __init__(self) -> None:
        pass

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
        query_contract: QueryExecutionContract,
        *,
        continuation_type: str | None = None,
        continuation_delta_type: str | None = None,
    ) -> dict[str, Any]:
        """Build compact debug metadata from the execution contract."""
        time_granularity = None
        if query_contract.time_range:
            time_granularity = query_contract.time_range.granularity

        time_window: dict[str, Any] = {
            "start": query_contract.time_start.isoformat(),
            "end": query_contract.time_end.isoformat(),
            "timezone": query_contract.timezone,
        }
        if time_granularity:
            time_window["granularity"] = time_granularity

        comparison_payload: dict[str, Any] | None = None
        if query_contract.comparison:
            comparison_payload = {"mode": query_contract.comparison.mode}
            if query_contract.comparison.explicit_range:
                comparison_payload["start"] = query_contract.comparison.explicit_range.start.isoformat()
                comparison_payload["end"] = query_contract.comparison.explicit_range.end.isoformat()

        return {
            "intent": query_contract.intent.value,
            "time_window": time_window,
            "comparison": comparison_payload,
            "filters": query_contract.filters.model_dump(exclude_none=True) if query_contract.filters else None,
            "aggregation": query_contract.aggregation.model_dump(exclude_none=True) if query_contract.aggregation else None,
            "result_limit": query_contract.result_limit,
            "result_reference": query_contract.result_reference,
            "continuation_type": query_contract.continuation_type or continuation_type,
            "continuation_delta_type": query_contract.continuation_delta_type or continuation_delta_type,
        }

    async def run(self, state: dict[str, Any], worker_context: Any = None) -> TransactionResult:
        """Run execution logic."""
        flow_state = state.get("flow_state")
        if flow_state != "executing":
            return TransactionResult(outcome=TransactionOutcome.OK, patch={})
        locale = LocaleManager.normalize(state.get("language")).value

        query_contract = state.get("query_contract")
        if isinstance(query_contract, dict):
            query_contract = QueryExecutionContract.model_validate(query_contract)

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

        if "selected_item_index" in state and state.get("query_session"):
            return await handle_drill_down(state)

        if not query_contract or not account_id:
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=render_message("query.error.missing_params", locale),
            )

        executor = QueryExecutor(worker_context.banking_provider)
        resolved_account_id = str(account_id)

        result = await executor.execute(
            query=query_contract,
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
        result.interpretation = self._build_interpretation(
            query_contract,
            continuation_type=state.get("continuation_type"),
            continuation_delta_type=state.get("continuation_delta_type"),
        )
        result = select_answer_strategy(result, locale=locale)
        result.surface_view = build_surface_view(result)

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
                "query_contract": query_contract,
                "resolver_message": None,
                "session_active": True,
                "flow_state": "complete",
                "cached_transactions": result.cached_transactions,
                "cache_fetched_at": result.cache_fetched_at,
                "cache_fingerprint": result.cache_fingerprint,
                "cache_scope_fingerprint": result.cache_scope_fingerprint,
                "cache_window_start": result.cache_window_start,
                "cache_window_end": result.cache_window_end,
            },
        )
