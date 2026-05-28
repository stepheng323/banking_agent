"""Aggregate continuation compilation for active query sessions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

from apps.chat.src.agent.workers.query.continuations.transforms import rebuild_query_contract
from apps.chat.src.agent.workers.query.models.domain import (
    Aggregation,
    QueryExecutionContract,
    QueryIntent,
)
from apps.chat.src.agent.workers.query.models.extraction import (
    QueryExtractionResult,
    QueryRequestShape,
    ResolverOutcome,
    TimeReference,
)
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
            bool(getattr(filters, "exclude", None)),
            bool(getattr(filters, "account_filter", None)),
        )
    )


def _should_reset_inherited_aggregate_filters(
    *,
    extraction: QueryExtractionResult | None,
    session_query_contract: QueryExecutionContract | None,
    extracted_contract: QueryExecutionContract | None,
) -> bool:
    if extraction is None or session_query_contract is None or extracted_contract is None:
        return False
    if extraction.time_range.reference_type not in {TimeReference.EXPLICIT, TimeReference.ALL_TIME}:
        return False
    if not _has_specific_scope_filters(session_query_contract.filters):
        return False
    return not _has_specific_scope_filters(extracted_contract.filters)


def _should_replace_aggregate_time_range(
    *,
    extraction: QueryExtractionResult | None,
    extracted_contract: QueryExecutionContract | None,
) -> bool:
    if extraction is None or extracted_contract is None:
        return False
    return extraction.time_range.reference_type in {TimeReference.EXPLICIT, TimeReference.ALL_TIME}


def _should_replace_aggregate_session_from_fresh_parse(
    *,
    session_query_contract: QueryExecutionContract,
    parsed_extraction: QueryExtractionResult | None,
    parsed_query_contract: QueryExecutionContract | None,
) -> bool:
    if parsed_extraction is None or parsed_query_contract is None:
        return False
    if parsed_extraction.time_range.reference_type not in {TimeReference.EXPLICIT, TimeReference.ALL_TIME}:
        return False
    if parsed_query_contract.intent != session_query_contract.intent:
        return True
    if _has_specific_scope_filters(session_query_contract.filters) and not _has_specific_scope_filters(
        parsed_query_contract.filters
    ):
        return True
    session_agg = session_query_contract.aggregation.type if session_query_contract.aggregation is not None else None
    parsed_agg = parsed_query_contract.aggregation.type if parsed_query_contract.aggregation is not None else None
    return session_agg != parsed_agg


def _should_override_aggregate_reasoner_extraction(
    *,
    extracted_contract: QueryExecutionContract | None,
    deterministic_contract: QueryExecutionContract | None,
) -> bool:
    if extracted_contract is None or deterministic_contract is None:
        return False
    if extracted_contract.intent != deterministic_contract.intent:
        return True
    extracted_agg = (
        extracted_contract.aggregation.type,
        extracted_contract.aggregation.group_by,
        extracted_contract.aggregation.sort_by,
    ) if extracted_contract.aggregation is not None else (None, None, None)
    deterministic_agg = (
        deterministic_contract.aggregation.type,
        deterministic_contract.aggregation.group_by,
        deterministic_contract.aggregation.sort_by,
    ) if deterministic_contract.aggregation is not None else (None, None, None)
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
            "query_operation": None,
        }
    )


async def compile_aggregate_continuation_updates(
    step: Any,
    *,
    decision: Any,
    state: dict[str, Any],
    today: date,
    language: str,
    session_query_contract: QueryExecutionContract | None,
    parse_result_to_updates: ParseResultToUpdates,
    parse_reasoner_extraction_to_updates: ParseReasonerExtractionToUpdates,
) -> dict[str, Any] | None:
    extraction = getattr(decision, "extraction", None)
    if extraction is not None and not extraction.raw_query:
        extraction = extraction.model_copy(update={"raw_query": state.get("message", "")})
    if extraction is not None:
        extraction = _sanitize_aggregate_extraction(extraction)

    deterministic_result = None
    deterministic_contract: QueryExecutionContract | None = None
    if session_query_contract is not None:
        deterministic_result = step.parser.parse_deterministic(state.get("message", ""), today=today, language=language)
        deterministic_contract = step._validated_query_contract(
            deterministic_result.query_contract if deterministic_result is not None else None
        )

    if extraction is None and session_query_contract is not None:
        if (
            deterministic_result is not None
            and deterministic_result.outcome == ResolverOutcome.OK
            and _should_replace_aggregate_session_from_fresh_parse(
                session_query_contract=session_query_contract,
                parsed_extraction=deterministic_result.extraction,
                parsed_query_contract=deterministic_contract,
            )
        ):
            logger.info(
                "query_aggregate_fresh_parse_reset",
                previous_intent=session_query_contract.intent.value,
                parsed_intent=deterministic_contract.intent.value if deterministic_contract is not None else None,
                parsed_time_reference=(
                    deterministic_result.extraction.time_range.reference_type.value
                    if deterministic_result.extraction is not None
                    else None
                ),
            )
            updates = parse_result_to_updates(step, deterministic_result, state=state, today=today, language=language)
            return step._append_query_session_transition(updates, "replace_session_new_query")

    if session_query_contract is None:
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

    extracted_contract: QueryExecutionContract | None = None
    if extraction is not None:
        compiled = step.parser.compile_extraction(extraction, today=today, language=language)
        extracted_contract = step._validated_query_contract(compiled.query_contract)
        if (
            deterministic_result is not None
            and deterministic_result.outcome == ResolverOutcome.OK
            and deterministic_contract is not None
            and _should_replace_aggregate_session_from_fresh_parse(
                session_query_contract=session_query_contract,
                parsed_extraction=deterministic_result.extraction,
                parsed_query_contract=deterministic_contract,
            )
            and _should_override_aggregate_reasoner_extraction(
                extracted_contract=extracted_contract,
                deterministic_contract=deterministic_contract,
            )
        ):
            logger.info(
                "query_aggregate_reasoner_extraction_reparsed",
                previous_intent=session_query_contract.intent.value,
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
    reset_inherited_filters = _should_reset_inherited_aggregate_filters(
        extraction=extraction,
        session_query_contract=session_query_contract,
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
            previous_intent=session_query_contract.intent.value,
            previous_has_specific_filters=_has_specific_scope_filters(session_query_contract.filters),
            extracted_intent=extracted_contract.intent.value if extracted_contract is not None else None,
            extracted_time_reference=extraction.time_range.reference_type.value if extraction is not None else None,
        )
    if extracted_contract is not None and extracted_contract.aggregation is not None:
        if (
            deterministic_result is not None
            and deterministic_result.outcome == ResolverOutcome.OK
            and deterministic_contract is not None
            and _should_replace_aggregate_session_from_fresh_parse(
                session_query_contract=session_query_contract,
                parsed_extraction=deterministic_result.extraction,
                parsed_query_contract=deterministic_contract,
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
                previous_intent=session_query_contract.intent.value,
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
    elif step._is_income_vs_spending_followup(message=state.get("message", ""), query_contract=session_query_contract):
        aggregation = Aggregation(type="breakdown", group_by="transaction_type")
        logger.info(
            "query_continuation_resolution",
            path="aggregate_income_vs_spending_fallback",
            recovered=True,
            original_intent=session_query_contract.intent.value,
        )
    else:
        aggregation = Aggregation(type="sum")

    query_contract = rebuild_query_contract(
        session_query_contract,
        filters=(
            updated_filters
            if reset_inherited_filters or updated_filters is not None
            else session_query_contract.filters
        ),
        merge_filters=updated_filters is not None and not reset_inherited_filters,
        time_range=(
            extracted_contract.to_query_ir().time_range
            if replace_time_range and extracted_contract is not None
            else session_query_contract.to_query_ir().time_range
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
        aggregation_type=query_contract.aggregation.type if query_contract.aggregation is not None else None,
        time_start=query_contract.time_start.isoformat(),
        time_end=query_contract.time_end.isoformat(),
        has_filters=bool(query_contract.filters),
    )
    return {
        "query_contract": query_contract,
        "resolver_message": None,
        "flow_state": "executing",
        "current_page": 0,
        "session_active": True,
        "pending_clarification": None,
        "show_expanded": False,
    }
