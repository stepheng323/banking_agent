from banking.transactions.query.continuations.aggregate_continuations import _sanitize_aggregate_extraction
from banking.transactions.query.models.extraction import (
    FactQueryKind,
    QueryExtractionResult,
    QueryRequestShape,
)


def test_sanitize_aggregate_extraction_clears_fact_and_limit_fields() -> None:
    extraction = QueryExtractionResult(
        answer_fact_field="date",
        fact_query_kind=FactQueryKind.DATE,
        request_shape=QueryRequestShape.FACT,
        result_limit=1,
        result_reference="latest",
    )

    sanitized = _sanitize_aggregate_extraction(extraction)

    assert sanitized.answer_fact_field is None
    assert sanitized.fact_query_kind is None
    assert sanitized.request_shape == QueryRequestShape.ANALYTICS
    assert sanitized.result_limit is None
    assert sanitized.result_reference is None
