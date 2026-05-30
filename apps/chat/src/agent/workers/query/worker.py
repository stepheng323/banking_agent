"""Query Worker.

Stateless worker for Query tasks.
Executes: Extract headers -> Parse/Continuity -> Execute -> Format.
Manages session persistence via Redis.
"""

from datetime import date
from types import SimpleNamespace
from typing import Any, cast

from langchain_core.runnables import Runnable

from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from apps.chat.src.agent.workers.query.grounding.frames import append_query_frame, restore_query_frames
from apps.chat.src.agent.workers.query.models.domain import (
    Filters,
    QueryExecutionContract,
    QueryIntent,
    QueryResult,
    TimeRange,
)
from apps.chat.src.agent.workers.query.nodes.execution import ExecutionStep
from apps.chat.src.agent.workers.query.nodes.extraction import ExtractionStep
from apps.chat.src.agent.workers.query.pipeline import QueryPipeline
from apps.chat.src.agent.workers.query.session import (
    QuerySessionManager,
    _session_has_surface_view,
    is_query_session_stale,
)
from apps.chat.src.agent.workers.query.utils.timezone import lagos_today
from banking.presentation.formatters.transaction_copy_context import build_copy_context
from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.renderer import render_message
from shared.clients.abstractions.banking import BankDataProvider
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class _InstrumentedQueryPipeline:
    def __init__(self, worker: "QueryWorker") -> None:
        self._worker = worker

    async def run(self, state: dict[str, Any], worker_context: Any = None) -> TransactionResult:
        return await self._worker._run_pipeline(state, worker_context)


class QueryWorker:
    """Worker for executing query tasks."""

    def __init__(
        self,
        llm: Runnable,
        banking_provider: BankDataProvider,
        session_manager: QuerySessionManager,
    ):
        self.llm = llm
        self.banking_provider = banking_provider
        self.session_manager = session_manager

        self.extractor = ExtractionStep(llm)
        self.executor = ExecutionStep()
        self._default_pipeline = QueryPipeline([self.extractor, self.executor])
        self.pipeline: Any = _InstrumentedQueryPipeline(self)

    @staticmethod
    def _query_context_mode(state: dict[str, Any]) -> str:
        query_session = state.get("query_session")
        if not isinstance(query_session, dict) or not query_session.get("session_active"):
            return "fresh"
        if query_session.get("pending_clarification"):
            return "pending_clarification"
        return "active_result"

    @staticmethod
    def _surface_type_name(value: Any) -> str | None:
        mode = value.get("mode") if isinstance(value, dict) else getattr(getattr(value, "mode", None), "value", None)
        mode_map = {
            "direct_answer": "single_item",
            "transaction_list": "list",
            "grouped_summary": "summary",
            "clarification": "clarification",
        }
        return mode_map.get(mode) if isinstance(mode, str) else None

    @classmethod
    def _surface_type_name_from_state(cls, state: dict[str, Any]) -> str | None:
        query_result = state.get("query_result")
        if isinstance(query_result, dict):
            return cls._surface_type_name(query_result.get("surface_view"))
        return cls._surface_type_name(getattr(query_result, "surface_view", None))

    @classmethod
    def _log_session_shape(
        cls,
        *,
        event: str,
        session: dict[str, Any] | None,
        session_source: str,
    ) -> None:
        snapshot = session if isinstance(session, dict) else {}
        logger.info(
            event,
            session_source=session_source,
            session_active=bool(snapshot.get("session_active")),
            has_query_contract=bool(snapshot.get("query_contract")),
            has_query_result=bool(snapshot.get("query_result")),
            has_surface=_session_has_surface_view(snapshot),
            has_query_frames=bool(snapshot.get("query_frames")),
            has_pending_clarification=bool(snapshot.get("pending_clarification")),
        )

    def _log_turn_summary(
        self,
        *,
        state: dict[str, Any],
        result: TransactionResult,
        session_source: str,
        restored_from_stashed_query_session: bool,
    ) -> None:
        patch = result.patch or {}
        final_state = {**state, **patch}
        session_transition = patch.get("_query_session_transition")
        if session_transition:
            logger.info(
                "query_session_transition",
                transition=session_transition,
                context_mode=self._query_context_mode(state),
                semantic_decision=patch.get("_query_semantic_decision"),
                session_active=final_state.get("session_active"),
            )
        logger.info(
            "query_turn_summary",
            context_mode=self._query_context_mode(state),
            semantic_decision=patch.get("_query_semantic_decision"),
            semantic_context_mode=patch.get("_query_semantic_context_mode"),
            semantic_llm_used=patch.get("_query_semantic_llm_used"),
            deterministic_surface_action=patch.get("_query_deterministic_surface_action"),
            session_transition=session_transition,
            outcome=result.outcome.value if hasattr(result.outcome, "value") else str(result.outcome),
            flow_state=final_state.get("flow_state"),
            surface_type=self._surface_type_name_from_state(final_state),
            session_active=final_state.get("session_active"),
            has_pending_clarification=bool(final_state.get("pending_clarification")),
            session_source=session_source,
            restored_from_stashed_query_session=restored_from_stashed_query_session,
        )

    @staticmethod
    def _finalize_result(result: TransactionResult, state: dict[str, Any]) -> TransactionResult:
        if result.patch is None:
            result.patch = {}
        result.patch.update(state)
        return result

    @staticmethod
    def _format_progress_date(value: date) -> str:
        return value.strftime("%b %d").replace(" 0", " ")

    @classmethod
    def _build_time_phrase(cls, time_range: TimeRange | None, locale: str) -> str | None:
        if time_range is None:
            return None

        today = lagos_today()
        if time_range.start == time_range.end == today:
            return render_message("progress.time.today", locale)

        this_month_start = today.replace(day=1)
        if time_range.start == this_month_start and time_range.end == today:
            return render_message("progress.time.this_month", locale)

        return render_message(
            "progress.time.range",
            locale,
            {
                "start": cls._format_progress_date(time_range.start),
                "end": cls._format_progress_date(time_range.end),
            },
        )

    @classmethod
    def _build_scope_label(
        cls,
        *,
        intent: QueryIntent,
        filters: Filters | None,
        time_range: TimeRange | None,
        locale: str,
        include_time: bool,
    ) -> str | None:
        counterparty_values = filters.counterparty if filters and filters.counterparty else []
        merchant_values = filters.merchant if filters and filters.merchant else []
        category_values = filters.category if filters and filters.category else []
        merchant = next((item.strip() for item in counterparty_values if isinstance(item, str) and item.strip()), None)
        if merchant is None:
            merchant = next((item.strip() for item in merchant_values if isinstance(item, str) and item.strip()), None)
        category = next((item.strip().title() for item in category_values if isinstance(item, str) and item.strip()), None)
        tx_type = filters.transaction_type if filters else None

        if merchant:
            if intent == QueryIntent.ANALYTICS_SUMMARY and tx_type == "debit":
                scope_label = render_message("progress.scope.sent_to", locale, {"counterparty": merchant})
            elif intent == QueryIntent.ANALYTICS_SUMMARY and tx_type == "credit":
                scope_label = render_message("progress.scope.received_from", locale, {"counterparty": merchant})
            else:
                scope_label = render_message("progress.scope.transactions_with", locale, {"counterparty": merchant})
        elif intent == QueryIntent.ANALYTICS_SUMMARY and tx_type == "debit":
            scope_label = render_message("progress.scope.sent", locale)
        elif intent == QueryIntent.ANALYTICS_SUMMARY and tx_type == "credit":
            scope_label = render_message("progress.scope.received", locale)
        elif intent == QueryIntent.ANALYTICS_SUMMARY:
            scope_label = render_message("progress.scope.spending", locale)
        elif tx_type == "debit":
            scope_label = render_message("progress.scope.outgoing_transactions", locale)
        elif tx_type == "credit":
            scope_label = render_message("progress.scope.incoming_transactions", locale)
        else:
            scope_label = render_message("progress.scope.transactions", locale)

        if category:
            scope_label = render_message(
                "progress.scope.with_category",
                locale,
                {"scope_label": scope_label, "category": category},
            )

        if include_time:
            time_phrase = cls._build_time_phrase(time_range, locale)
            if time_phrase:
                scope_label = render_message(
                    "progress.scope.with_time",
                    locale,
                    {"scope_label": scope_label, "time_phrase": time_phrase},
                )

        return scope_label

    @classmethod
    def _build_query_progress_stage_metadata(
        cls,
        query_contract: QueryExecutionContract | dict[str, Any] | None,
        *,
        locale: str,
        include_time: bool,
    ) -> dict[str, str] | None:
        if isinstance(query_contract, dict):
            try:
                query_contract = QueryExecutionContract.model_validate(query_contract)
            except Exception:
                return None
        if not isinstance(query_contract, QueryExecutionContract):
            return None

        filters = query_contract.filters
        counterparty_values = filters.counterparty if filters and filters.counterparty else []
        merchant_values = filters.merchant if filters and filters.merchant else []
        category_values = filters.category if filters and filters.category else []
        merchant = next((item.strip() for item in counterparty_values if isinstance(item, str) and item.strip()), None)
        if merchant is None:
            merchant = next((item.strip() for item in merchant_values if isinstance(item, str) and item.strip()), None)
        category = next((item.strip().title() for item in category_values if isinstance(item, str) and item.strip()), None)
        tx_type = filters.transaction_type if filters else None
        time_phrase = cls._build_time_phrase(query_contract.time_range, locale) if include_time else None
        scope_label = cls._build_scope_label(
            intent=query_contract.intent,
            filters=filters,
            time_range=query_contract.time_range,
            locale=locale,
            include_time=include_time,
        )

        direction = "all"
        if query_contract.intent == QueryIntent.ANALYTICS_SUMMARY and tx_type == "debit":
            direction = "sent"
        elif query_contract.intent == QueryIntent.ANALYTICS_SUMMARY and tx_type == "credit":
            direction = "received"
        elif tx_type == "debit":
            direction = "outgoing"
        elif tx_type == "credit":
            direction = "incoming"

        metadata = build_copy_context(
            task_type="query",
            payload={
                "direction": direction,
                "counterparty_label": merchant,
                "recipient_name": merchant,
                "time_label": time_phrase,
                "scope_label": scope_label,
            },
        )
        metadata["intent_family"] = query_contract.intent.value
        if merchant:
            metadata["counterparty_label"] = merchant
        if category:
            metadata["category_label"] = category
        if time_phrase:
            metadata["time_label"] = time_phrase
        if scope_label:
            metadata["scope_label"] = scope_label
        return metadata

    @classmethod
    async def _set_query_progress_stage(
        cls,
        stage_key: str,
        *,
        state: dict[str, Any],
        worker_context: SimpleNamespace,
        include_time: bool,
    ) -> None:
        progress_tracker = getattr(worker_context, "progress_tracker", None)
        if progress_tracker is None:
            return

        locale = LocaleManager.normalize(state.get("language")).value
        stage_metadata = cls._build_query_progress_stage_metadata(
            state.get("query_contract"),
            locale=locale,
            include_time=include_time,
        )
        await progress_tracker.set_stage(stage_key, stage_metadata=stage_metadata)

    @staticmethod
    async def _set_execution_progress_stage(state: dict[str, Any], worker_context: SimpleNamespace) -> None:
        query_contract = state.get("query_contract")
        if isinstance(query_contract, dict):
            query_contract = QueryExecutionContract.model_validate(query_contract)

        stage_key = "query.fetching_transactions"
        if isinstance(query_contract, QueryExecutionContract):
            if query_contract.comparison is not None or query_contract.intent == QueryIntent.TIME_COMPARISON:
                stage_key = "query.comparing_periods"

        await QueryWorker._set_query_progress_stage(
            stage_key,
            state=state,
            worker_context=worker_context,
            include_time=False,
        )

    async def run(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        """Run the query pipeline."""
        del user_message, pin_verified
        locale = LocaleManager.normalize(context.get("language")).value

        # 1. Load Session
        phone_number = context.get("phone_number")
        session_key = f"query:session:{phone_number}"
        query_session = await self.session_manager.load(session_key) or {}
        restored_from_stashed_query_session = False
        session_source = "redis" if query_session else "none"
        if query_session:
            self._log_session_shape(
                event="query_session_loaded",
                session=query_session,
                session_source=session_source,
            )
        if query_session and not query_session.get("query_contract") and not query_session.get("pending_clarification"):
            logger.warning(
                "query_session_missing_contract_cleared",
                session_source=session_source,
                session_active=bool(query_session.get("session_active")),
                has_query_result=bool(query_session.get("query_result")),
                has_surface=_session_has_surface_view(query_session),
                has_query_frames=bool(query_session.get("query_frames")),
            )
            await self.session_manager.clear(session_key)
            query_session = {}
            session_source = "none"

        if not query_session and isinstance(context.get("stashed_query_session"), dict):
            stashed_query_session = dict(cast(dict[str, Any], context["stashed_query_session"]))
            if is_query_session_stale(stashed_query_session):
                logger.info("stashed_query_session_stale", phone_number=phone_number)
                stashed_query_session = {}
            raw_contract = stashed_query_session.get("query_contract")
            if raw_contract:
                try:
                    contract = (
                        raw_contract
                        if isinstance(raw_contract, QueryExecutionContract)
                        else QueryExecutionContract.model_validate(raw_contract)
                    )
                    stashed_query_session["query_contract"] = contract.model_dump()
                    if stashed_query_session.get("query_frames") is not None:
                        stashed_query_session["query_frames"] = restore_query_frames(stashed_query_session.get("query_frames"))
                    query_session = stashed_query_session
                    restored_from_stashed_query_session = True
                    session_source = "stashed"
                    self._log_session_shape(
                        event="query_session_restored_from_stash",
                        session=query_session,
                        session_source=session_source,
                    )
                except Exception:
                    logger.warning("stashed_query_session_invalid_contract_ignored")

        today_context = context.get("today")
        today = today_context if isinstance(today_context, date) else lagos_today()

        # Merge key session fields into state so continuation steps have context.
        session_defaults = {
            "query_contract": query_session.get("query_contract"),
            "query_result": query_session.get("query_result"),
            "show_expanded": query_session.get("show_expanded"),
            "current_page": query_session.get("current_page"),
            "page_size": query_session.get("page_size"),
            "account_id": query_session.get("account_id"),
            "account_ids": query_session.get("account_ids"),
            "cached_transactions": query_session.get("cached_transactions"),
            "cache_fetched_at": query_session.get("cache_fetched_at"),
            "cache_fingerprint": query_session.get("cache_fingerprint"),
            "cache_scope_fingerprint": query_session.get("cache_scope_fingerprint"),
            "cache_window_start": query_session.get("cache_window_start"),
            "cache_window_end": query_session.get("cache_window_end"),
            "pending_clarification": query_session.get("pending_clarification"),
            "query_frames": query_session.get("query_frames"),
            "selected_item_index": query_session.get("selected_item_index"),
        }

        # 2. Build Initial State
        state = {
            "message": payload.get("message", ""),
            "force_new_query": bool(payload.get("force_new_query")),
            "phone_number": phone_number,
            "account_id": payload.get("account_id"),
            "account_ids": payload.get("account_ids"),
            "accounts": context.get("accounts", []),
            "language": locale,
            "turn_id": context.get("turn_id") or context.get("inbound_message_id"),
            "inbound_message_id": context.get("inbound_message_id") or context.get("turn_id"),
            "query_session": query_session,
            "flow_state": "parsing",
            "current_page": query_session.get("current_page", 0),
            "page_size": 5,
            "today": today,
        }

        for key, value in session_defaults.items():
            if value is not None and state.get(key) is None:
                state[key] = value

        # 3. Setup Worker Context
        worker_context = SimpleNamespace(
            banking_provider=self.banking_provider,
            user_id=context.get("user_id"),
            progress_tracker=context.get("progress_tracker"),
        )

        if (
            isinstance(query_session, dict)
            and query_session.get("session_active")
            and not query_session.get("query_contract")
            and not query_session.get("pending_clarification")
        ):
            logger.warning(
                "query_active_session_missing_continuation_context",
                session_source=session_source,
                has_query_result=bool(query_session.get("query_result")),
                has_surface=_session_has_surface_view(query_session),
                has_query_frames=bool(query_session.get("query_frames")),
            )

        if (
            isinstance(query_session, dict)
            and query_session.get("session_active")
            and not query_session.get("pending_clarification")
            and not state["force_new_query"]
        ):
            await self._set_query_progress_stage(
                "query.resolving_followup",
                state=state,
                worker_context=worker_context,
                include_time=False,
            )

        # 4. Run Pipeline
        try:
            result = cast(TransactionResult, await self.pipeline.run(state, worker_context))
            self._log_turn_summary(
                state=state,
                result=result,
                session_source=session_source,
                restored_from_stashed_query_session=restored_from_stashed_query_session,
            )

            # 5. Handle Session Persistence
            if result.outcome in (TransactionOutcome.OK, TransactionOutcome.NEEDS_INPUT) and result.patch:
                final_state = {**state, **result.patch}
                session_active = final_state.get("session_active", False)

                if session_active:
                    query_contract = result.patch.get("query_contract")
                    query_result = result.patch.get("query_result")
                    query_frames = restore_query_frames(final_state.get("query_frames"))
                    if isinstance(query_contract, dict):
                        try:
                            query_contract = QueryExecutionContract.model_validate(query_contract)
                        except Exception:
                            query_contract = None
                    if isinstance(query_result, dict):
                        try:
                            query_result = QueryResult.model_validate(query_result)
                        except Exception:
                            query_result = None
                    if query_contract and isinstance(query_result, QueryResult):
                        final_state["query_frames"] = append_query_frame(
                            query_frames,
                            query_contract=query_contract,
                            result=query_result,
                        )
                    final_state["timestamp"] = __import__("time").time()
                    await self.session_manager.save(session_key, final_state)
                else:
                    await self.session_manager.clear(session_key)

                if restored_from_stashed_query_session:
                    result.patch["restored_from_stashed_query_session"] = True

            return result

        except Exception as e:
            logger.error("query_worker_error", error=str(e), exc_info=True)
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=render_message("query.error.general", locale),
            )

    async def _run_pipeline(
        self,
        state: dict[str, Any],
        worker_context: Any = None,
    ) -> TransactionResult:
        extraction_result = cast(TransactionResult, await self.extractor.run(state, worker_context))
        if extraction_result.patch:
            state.update(extraction_result.patch)

        if extraction_result.outcome != TransactionOutcome.OK:
            return self._finalize_result(extraction_result, state)
        if state.get("flow_state") != "executing":
            return self._finalize_result(extraction_result, state)

        await self._set_execution_progress_stage(state, cast(SimpleNamespace, worker_context))
        execution_result = cast(TransactionResult, await self.executor.run(state, worker_context))
        if execution_result.patch:
            state.update(execution_result.patch)
        return self._finalize_result(execution_result, state)
