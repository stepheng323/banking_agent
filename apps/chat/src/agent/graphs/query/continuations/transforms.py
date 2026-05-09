"""Contract-native continuation transform helpers."""

from __future__ import annotations

from typing import Any, cast

from apps.chat.src.agent.graphs.query.models import Filters, QueryExecutionContract, TimeRange

_UNCHANGED = object()


def rebuild_query_contract(
    original_contract: QueryExecutionContract,
    *,
    filters: Filters | object = _UNCHANGED,
    merge_filters: bool = False,
    time_range: TimeRange | object = _UNCHANGED,
    intent: Any = _UNCHANGED,
    query_operation: Any = _UNCHANGED,
    aggregation: Any = _UNCHANGED,
    result_limit: int | None | object = _UNCHANGED,
    result_reference: str | None | object = _UNCHANGED,
    answer_fact_field: str | None | object = _UNCHANGED,
    comparison: Any = _UNCHANGED,
    continuation_type: str | None | object = _UNCHANGED,
    continuation_delta_type: str | None | object = _UNCHANGED,
) -> QueryExecutionContract:
    """Rebuild a fresh runtime contract from a base contract plus explicit overrides."""
    ir = original_contract.to_query_ir()

    if filters is not _UNCHANGED:
        if merge_filters and isinstance(filters, Filters):
            base_filters = ir.filters.model_copy(deep=True) if ir.filters is not None else Filters()
            new_filters = filters.model_dump(exclude_none=True)
            merged = base_filters.model_dump(exclude_none=True)
            for key, value in new_filters.items():
                if key == "exclude" and merged.get("exclude"):
                    merged["exclude"] = merged["exclude"] + value
                else:
                    merged[key] = value
            ir.filters = Filters.model_validate(merged)
        else:
            ir.filters = filters.model_copy(deep=True) if isinstance(filters, Filters) else None

    if time_range is not _UNCHANGED:
        ir.time_range = time_range.model_copy(deep=True) if isinstance(time_range, TimeRange) else ir.time_range
    if intent is not _UNCHANGED:
        ir.intent = intent
    if query_operation is not _UNCHANGED:
        ir.query_operation = query_operation
    if aggregation is not _UNCHANGED:
        ir.aggregation = aggregation.model_copy(deep=True) if aggregation is not None else None
    if result_limit is not _UNCHANGED:
        ir.result_limit = cast(int | None, result_limit)
    if result_reference is not _UNCHANGED:
        ir.result_reference = cast(Any, result_reference)
    if answer_fact_field is not _UNCHANGED:
        ir.answer_fact_field = cast(Any, answer_fact_field)
    if comparison is not _UNCHANGED:
        ir.comparison = comparison.model_copy(deep=True) if comparison is not None else None
    if continuation_type is not _UNCHANGED:
        ir.continuation_type = cast(str | None, continuation_type)
    if continuation_delta_type is not _UNCHANGED:
        ir.continuation_delta_type = cast(str | None, continuation_delta_type)

    return QueryExecutionContract.from_query_ir(ir)
