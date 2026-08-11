"""Aggregate continuation compilation for active query sessions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

from banking.transactions.query.compiler.aggregation import build_default_aggregation
from banking.transactions.query.continuations.transforms import rebuild_query_request
from banking.transactions.query.models.domain import (
    Aggregation,
    QueryIntent,
    QueryRequest,
)
from banking.transactions.query.models.extraction import (
    QueryExtractionResult,
    QueryRequestShape,
    ResolverOutcome,
    TimeReference,
)
from banking.transactions.query.models.operations import AnalyzeOperation
from shared.utils.logging import get_logger

logger = get_logger(__name__)

ParseResultToUpdates = Callable[..., dict[str, Any]]
ParseReasonerExtractionToUpdates = Callable[..., Awaitable[dict[str, Any]]]


def _has_specific_scope_filters(filters: Any | None) -> bool:
    if filters is None:
        return False
    return any(
        (
            bool(getattr(filters, "merchant", None)),
            bool(getattr(filters, "counterparty", None)),
            bool(getattr(filters, "category", None)),
            getattr(filters, "min_amount", None) is not None,
            getattr(filters, "max_amount", None) is not None,
            getattr(filters, "status", None) is not None,
            bool(getattr(filters, "exclude", None)),
            bool(getattr(filters, "account_filter", None)),
        )
    )


def _should_reset_inherited_aggregate_filters(
    *,
    extraction: QueryExtractionResult | None,
    session_query_request: QueryRequest | None,
    extracted_contract: QueryRequest | None,
) -> bool:
    if extraction is None or session_query_request is None or extracted_contract is None:
        return False
    if extraction.time_range.reference_type not in {TimeReference.EXPLICIT, TimeReference.ALL_TIME}:
        return False
    if not _has_specific_scope_filters(session_query_request.filters):
        return False
    return not _has_specific_scope_filters(extracted_contract.filters)


def _should_replace_aggregate_time_range(
    *,
    extraction: QueryExtractionResult | None,
    extracted_contract: QueryRequest | None,
) -> bool:
    if extraction is None or extracted_contract is None:
        return False
    if extraction.time_range.reference_type == TimeReference.ALL_TIME:
        return True
    if extraction.time_range.reference_type == TimeReference.EXPLICIT:
        return bool(extraction.time_range.period or extraction.time_range.days_back)
    return False


def _should_replace_aggregate_session_from_fresh_parse(
    *,
    session_query_request: QueryRequest,
    parsed_extraction: QueryExtractionResult | None,
    parsed_query_request: QueryRequest | None,
) -> bool:
    if parsed_extraction is None or parsed_query_request is None:
        return False
    if parsed_extraction.time_range.reference_type not in {TimeReference.EXPLICIT, TimeReference.ALL_TIME}:
        return False
    if parsed_query_request.intent != session_query_request.intent:
        return True
    if _has_specific_scope_filters(session_query_request.filters) and not _has_specific_scope_filters(
        parsed_query_request.filters
    ):
        return True
    session_agg = session_query_request.aggregation.type if session_query_request.aggregation is not None else None
    parsed_agg = parsed_query_request.aggregation.type if parsed_query_request.aggregation is not None else None
    return session_agg != parsed_agg


def _should_override_aggregate_reasoner_extraction(
    *,
    extracted_contract: QueryRequest | None,
    deterministic_contract: QueryRequest | None,
) -> bool:
    if extracted_contract is None or deterministic_contract is None:
        return False
    if extracted_contract.intent != deterministic_contract.intent:
        return True
    extracted_agg = (
        (
            extracted_contract.aggregation.type,
            extracted_contract.aggregation.group_by,
            extracted_contract.aggregation.sort_by,
        )
        if extracted_contract.aggregation is not None
        else (None, None, None)
    )
    deterministic_agg = (
        (
            deterministic_contract.aggregation.type,
            deterministic_contract.aggregation.group_by,
            deterministic_contract.aggregation.sort_by,
        )
        if deterministic_contract.aggregation is not None
        else (None, None, None)
    )
    if extracted_agg != deterministic_agg:
        return True
    if _has_specific_scope_filters(extracted_contract.filters) and not _has_specific_scope_filters(
        deterministic_contract.filters
    ):
        return True
    return extracted_contract.answer_fact_field is not None or extracted_contract.result_reference is not None


def _sanitize_aggregate_extraction(extraction: QueryExtractionResult) -> QueryExtractionResult:
    return extraction.model_copy(
        update={
            "answer_fact_field": None,
            "fact_query_kind": None,
            "request_shape": QueryRequestShape.ANALYTICS,
            "result_limit": None,
            "result_reference": None,
        }
    )


def _preserve_active_direction_unless_filter_delta(
    *,
    decision: Any,
    session_query_request: QueryRequest,
    updated_filters: Any | None,
) -> Any | None:
    """Keep active credit/debit scope for grouping-only aggregate refinements."""
    if updated_filters is None:
        return updated_filters
    if getattr(decision, "delta_type", None) == "filter":
        return updated_filters

    active_filters = session_query_request.filters
    active_type = getattr(active_filters, "transaction_type", None) if active_filters is not None else None
    updated_type = getattr(updated_filters, "transaction_type", None)
    if active_type not in {"credit", "debit"} or updated_type not in {"credit", "debit"}:
        return updated_filters
    if active_type == updated_type:
        return updated_filters
    if hasattr(updated_filters, "model_copy"):
        return updated_filters.model_copy(update={"transaction_type": active_type})
    return updated_filters


async def compile_aggregate_continuation_updates(
    step: Any,
    *,
    decision: Any,
    state: dict[str, Any],
    today: date,
    language: str,
    session_query_request: QueryRequest | None,
    parse_result_to_updates: ParseResultToUpdates,
    parse_reasoner_extraction_to_updates: ParseReasonerExtractionToUpdates,
) -> dict[str, Any] | None:
    extraction = getattr(decision, "extraction", None)
    if getattr(decision, "plan", None) is not None:
        return await parse_reasoner_extraction_to_updates(
            step,
            decision,
            state=state,
            today=today,
            language=language,
        )
    if extraction is not None and not extraction.raw_query:
        extraction = extraction.model_copy(update={"raw_query": state.get("message", "")})
    if extraction is not None:
        extraction = _sanitize_aggregate_extraction(extraction)

    deterministic_result = None
    deterministic_contract: QueryRequest | None = None
    if session_query_request is not None:
        deterministic_result = step.parser.parse_deterministic(state.get("message", ""), today=today, language=language)
        deterministic_contract = step._validated_query_request(
            deterministic_result.query_request if deterministic_result is not None else None
        )

    if extraction is None and session_query_request is not None:
        if (
            deterministic_result is not None
            and deterministic_result.outcome == ResolverOutcome.OK
            and _should_replace_aggregate_session_from_fresh_parse(
                session_query_request=session_query_request,
                parsed_extraction=deterministic_result.extraction,
                parsed_query_request=deterministic_contract,
            )
        ):
            logger.info(
                "query_aggregate_fresh_parse_reset",
                previous_intent=session_query_request.intent.value,
                parsed_intent=deterministic_contract.intent.value if deterministic_contract is not None else None,
                parsed_time_reference=(
                    deterministic_result.extraction.time_range.reference_type.value
                    if deterministic_result.extraction is not None
                    else None
                ),
            )
            updates = parse_result_to_updates(step, deterministic_result, state=state, today=today, language=language)
            return step._append_query_session_transition(updates, "replace_session_new_query")

    if session_query_request is None:
        if extraction is None:
            return None
        patched_decision = (
            decision.model_copy(update={"extraction": extraction}) if hasattr(decision, "model_copy") else decision
        )
        return await parse_reasoner_extraction_to_updates(
            step,
            patched_decision,
            state=state,
            today=today,
            language=language,
        )

    # A rank over an existing grouped surface is a request about its buckets,
    # not a fresh transaction lookup. The reasoner supplies the semantic rank;
    # runtime keeps the already-grounded group, filters, and period and applies
    # the cardinality deterministically.
    active_aggregation = session_query_request.aggregation
    if (
        getattr(decision, "rank", None) in {"largest", "smallest"}
        and active_aggregation is not None
        and active_aggregation.group_by is not None
    ):
        ranked_aggregation = active_aggregation.model_copy(update={"limit": 1, "type": "breakdown"})
        query_request = rebuild_query_request(
            session_query_request,
            intent=QueryIntent.ANALYTICS_SUMMARY,
            aggregation=ranked_aggregation,
            result_limit=None,
            result_reference=None,
            answer_fact_field=None,
            continuation_type="aggregate",
            continuation_delta_type=getattr(decision, "delta_type", None),
        )
        logger.info(
            "query_grouped_rank_continuation_compiled",
            group_by=ranked_aggregation.group_by,
            rank=getattr(decision, "rank", None),
        )
        return {
            "query_request": query_request,
            "resolver_message": None,
            "flow_state": "executing",
            "current_page": 0,
            "session_active": True,
            "pending_input": None,
            "show_expanded": False,
        }

    # A typed aggregate continuation over an account breakdown is already
    # safely grounded.  If the provider omits the optional rank/extraction
    # patch, retain and replay that authoritative surface instead of replacing
    # it with a generic sum or asking the user to repeat an unambiguous
    # grouped question.  This is a conservative fallback: it exposes no new
    # data and preserves every filter, account scope, and period.
    if extraction is None and active_aggregation is not None and active_aggregation.group_by == "account":
        query_request = rebuild_query_request(
            session_query_request,
            intent=QueryIntent.ANALYTICS_SUMMARY,
            aggregation=active_aggregation,
            result_limit=None,
            result_reference=None,
            answer_fact_field=None,
            continuation_type="aggregate",
            continuation_delta_type=getattr(decision, "delta_type", None),
        )
        logger.info(
            "query_grouped_aggregate_scope_replayed",
            group_by=active_aggregation.group_by,
            reason="missing_optional_rank_or_extraction",
        )
        return {
            "query_request": query_request,
            "resolver_message": None,
            "flow_state": "executing",
            "current_page": 0,
            "session_active": True,
            "pending_input": None,
            "show_expanded": False,
        }

    extracted_contract: QueryRequest | None = None
    if extraction is not None:
        compiled = step.parser.compile_extraction(extraction, today=today, language=language)
        extracted_contract = step._validated_query_request(compiled.query_request)
        if (
            extracted_contract is not None
            and extracted_contract.intent == QueryIntent.INSIGHT
            and session_query_request.intent == QueryIntent.INSIGHT
            and isinstance(extracted_contract.operation, AnalyzeOperation)
            and isinstance(session_query_request.operation, AnalyzeOperation)
            and session_query_request.scope is not None
        ):
            # A variance follow-up changes the typed analysis directive, not the
            # user’s already-grounded period, filters, or accounts.  Rebuilding
            # from the fresh extraction would silently default its scope.
            analysis = extracted_contract.operation.analysis.model_copy(update={"evidence": None})
            query_request = QueryRequest(
                operation=AnalyzeOperation(scope=session_query_request.scope, analysis=analysis)
            )
            logger.info(
                "query_insight_refinement_compiled",
                insight_type=analysis.insight_type,
            )
            return {
                "query_request": query_request,
                "resolver_message": None,
                "flow_state": "executing",
                "current_page": 0,
                "session_active": True,
                "pending_input": None,
                "show_expanded": False,
            }
        if extraction.intent == QueryIntent.CASH_FLOW_SUMMARY and extracted_contract is not None:
            cashflow_contract = extracted_contract.model_copy(
                update={
                    "intent": QueryIntent.CASH_FLOW_SUMMARY,
                    "filters": None,
                    "aggregation": None,
                }
            )
            patched_result = compiled.model_copy(
                update={
                    "extraction": extraction,
                    "query_request": cashflow_contract.model_dump(mode="json"),
                    "query_ir": None,
                }
            )
            updates = parse_result_to_updates(step, patched_result, state=state, today=today, language=language)
            return step._append_query_session_transition(updates, "replace_session_new_query")
        if (
            deterministic_result is not None
            and deterministic_result.outcome == ResolverOutcome.OK
            and deterministic_contract is not None
            and _should_replace_aggregate_session_from_fresh_parse(
                session_query_request=session_query_request,
                parsed_extraction=deterministic_result.extraction,
                parsed_query_request=deterministic_contract,
            )
            and _should_override_aggregate_reasoner_extraction(
                extracted_contract=extracted_contract,
                deterministic_contract=deterministic_contract,
            )
        ):
            logger.info(
                "query_aggregate_reasoner_extraction_reparsed",
                previous_intent=session_query_request.intent.value,
                extracted_intent=extracted_contract.intent.value if extracted_contract is not None else None,
                parsed_intent=deterministic_contract.intent.value,
                extracted_aggregation_type=(
                    extracted_contract.aggregation.type
                    if extracted_contract is not None and extracted_contract.aggregation is not None
                    else None
                ),
                parsed_aggregation_type=(
                    deterministic_contract.aggregation.type if deterministic_contract.aggregation is not None else None
                ),
            )
            updates = parse_result_to_updates(step, deterministic_result, state=state, today=today, language=language)
            return step._append_query_session_transition(updates, "replace_session_new_query")
        if extracted_contract is not None and extracted_contract.intent in {
            QueryIntent.CASH_FLOW_SUMMARY,
            QueryIntent.TIME_COMPARISON,
            QueryIntent.BENEFICIARY_SUMMARY,
            QueryIntent.AFFORDABILITY,
        }:
            patched_decision = (
                decision.model_copy(update={"extraction": extraction}) if hasattr(decision, "model_copy") else decision
            )
            return await parse_reasoner_extraction_to_updates(
                step,
                patched_decision,
                state=state,
                today=today,
                language=language,
            )

    updated_filters = extracted_contract.filters if extracted_contract is not None else None
    updated_filters = _preserve_active_direction_unless_filter_delta(
        decision=decision,
        session_query_request=session_query_request,
        updated_filters=updated_filters,
    )
    reset_inherited_filters = _should_reset_inherited_aggregate_filters(
        extraction=extraction,
        session_query_request=session_query_request,
        extracted_contract=extracted_contract,
    )
    replace_time_range = _should_replace_aggregate_time_range(
        extraction=extraction,
        extracted_contract=extracted_contract,
    )
    if reset_inherited_filters:
        logger.info(
            "query_aggregate_filter_reset",
            reason="explicit_aggregate_scope_without_specific_filter",
            previous_intent=session_query_request.intent.value,
            previous_has_specific_filters=_has_specific_scope_filters(session_query_request.filters),
            extracted_intent=extracted_contract.intent.value if extracted_contract is not None else None,
            extracted_time_reference=extraction.time_range.reference_type.value if extraction is not None else None,
        )
    if extracted_contract is not None and extracted_contract.aggregation is not None:
        if (
            deterministic_result is not None
            and deterministic_result.outcome == ResolverOutcome.OK
            and deterministic_contract is not None
            and _should_replace_aggregate_session_from_fresh_parse(
                session_query_request=session_query_request,
                parsed_extraction=deterministic_result.extraction,
                parsed_query_request=deterministic_contract,
            )
            and (
                _has_specific_scope_filters(extracted_contract.filters)
                and not _has_specific_scope_filters(deterministic_contract.filters)
                or extracted_contract.answer_fact_field is not None
                or extracted_contract.result_reference is not None
            )
        ):
            logger.info(
                "query_aggregate_reasoner_extraction_reset",
                previous_intent=session_query_request.intent.value,
                extracted_intent=extracted_contract.intent.value,
                parsed_intent=deterministic_contract.intent.value,
                extracted_has_specific_filters=_has_specific_scope_filters(extracted_contract.filters),
                parsed_has_specific_filters=_has_specific_scope_filters(deterministic_contract.filters),
                extracted_answer_fact_field=extracted_contract.answer_fact_field,
                parsed_time_reference=(
                    deterministic_result.extraction.time_range.reference_type.value
                    if deterministic_result.extraction is not None
                    else None
                ),
            )
            updates = parse_result_to_updates(step, deterministic_result, state=state, today=today, language=language)
            return step._append_query_session_transition(updates, "replace_session_new_query")

        aggregation = extracted_contract.aggregation.model_copy(deep=True)
    elif (
        deterministic_result is not None
        and deterministic_result.outcome == ResolverOutcome.OK
        and deterministic_contract is not None
        and deterministic_contract.intent == QueryIntent.ANALYTICS_SUMMARY
        and deterministic_contract.aggregation is not None
    ):
        deterministic_aggregation = deterministic_contract.aggregation
        extracted_contract = deterministic_contract
        updated_filters = extracted_contract.filters
        updated_filters = _preserve_active_direction_unless_filter_delta(
            decision=decision,
            session_query_request=session_query_request,
            updated_filters=updated_filters,
        )
        aggregation = deterministic_aggregation.model_copy(deep=True)
        logger.info(
            "query_aggregate_deterministic_contract_applied",
            aggregation_type=aggregation.type,
            group_by=aggregation.group_by,
        )
    elif step._is_income_vs_spending_followup(message=state.get("message", ""), query_request=session_query_request):
        aggregation = Aggregation(type="breakdown", group_by="transaction_type")
        logger.info(
            "query_continuation_resolution",
            path="aggregate_income_vs_spending_fallback",
            recovered=True,
            original_intent=session_query_request.intent.value,
        )
    else:
        aggregation = build_default_aggregation(
            extraction if extraction is not None else QueryExtractionResult(raw_query=state.get("message", "")),
            intent=QueryIntent.ANALYTICS_SUMMARY,
        )
        if aggregation is None:
            aggregation = Aggregation(type="sum")

    # When the user switches the grouping dimension (e.g. food list -> "by banks"),
    # drop the old bucket filter so we don't show "food spending by bank".
    if aggregation.type == "breakdown" and aggregation.group_by is not None:
        filter_field_to_dimension = {
            "category": "category",
            "merchant": "merchant",
            "counterparty": "merchant",
            "account_filter": "account",
        }
        conflicting_filter_field = None
        for filter_field, dimension in filter_field_to_dimension.items():
            if getattr(session_query_request.filters, filter_field, None) and dimension != aggregation.group_by:
                conflicting_filter_field = filter_field
                break
        if conflicting_filter_field is not None:
            if updated_filters is None:
                updated_filters = session_query_request.filters.model_copy(deep=True)
            updated_filters = updated_filters.model_copy(update={conflicting_filter_field: None})
            reset_inherited_filters = True
            logger.info(
                "query_aggregate_dimension_switch_filter_dropped",
                dropped_filter=conflicting_filter_field,
                new_group_by=aggregation.group_by,
            )

    query_request = rebuild_query_request(
        session_query_request,
        filters=(
            updated_filters if reset_inherited_filters or updated_filters is not None else session_query_request.filters
        ),
        merge_filters=updated_filters is not None and not reset_inherited_filters,
        time_range=(
            extracted_contract.time_range
            if replace_time_range and extracted_contract is not None
            else session_query_request.time_range
        ),
        intent=QueryIntent.ANALYTICS_SUMMARY,
        aggregation=aggregation,
        result_limit=extracted_contract.result_limit if extracted_contract is not None else None,
        result_reference=extracted_contract.result_reference if extracted_contract is not None else None,
        answer_fact_field=None,
        continuation_type=getattr(decision, "continuation_type", None),
        continuation_delta_type=getattr(decision, "delta_type", None),
    )
    logger.info(
        "query_aggregate_continuation_compiled",
        source="reasoner_extraction" if extracted_contract is not None else "active_query_scope_default",
        aggregation_type=query_request.aggregation.type if query_request.aggregation is not None else None,
        time_start=query_request.time_start.isoformat(),
        time_end=query_request.time_end.isoformat(),
        has_filters=bool(query_request.filters),
    )
    return {
        "query_request": query_request,
        "resolver_message": None,
        "flow_state": "executing",
        "current_page": 0,
        "session_active": True,
        "pending_input": None,
        "show_expanded": False,
    }
