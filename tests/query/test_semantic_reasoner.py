from datetime import date

import pytest

from apps.core.src.agent.graphs.query.models import (
    Filters,
    NormalizedQuery,
    QueryExecutionContract,
    QueryExtractionResult,
    QueryFilters,
    QueryIntent,
    QueryResultItem,
    ResultSurface,
    SurfaceType,
    TimeRange,
)
from apps.core.src.agent.graphs.query.nodes.extraction import ExtractionStep
from apps.core.src.agent.graphs.query.services.reasoner import (
    QuerySemanticDecision,
    QuerySemanticReasoner,
    SemanticReasonerContext,
)
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome


class _FailingStructured:
    async def ainvoke(self, prompt: str) -> object:
        del prompt
        raise AssertionError("LLM should not be called")


class _FailingLLM:
    def with_structured_output(self, schema: object) -> _FailingStructured:
        del schema
        return _FailingStructured()


@pytest.mark.asyncio
async def test_reasoner_uses_deterministic_receipt_action_without_llm() -> None:
    reasoner = QuerySemanticReasoner(_FailingLLM())
    surface = ResultSurface(type=SurfaceType.SINGLE_ITEM, items=[], context={"type": "single_transaction"})

    decision = await reasoner.reason(
        SemanticReasonerContext(
            message="receipt",
            today=date(2026, 3, 13),
            language="en",
            query_contract=QueryExecutionContract(
                intent=QueryIntent.TRANSACTION_SEARCH,
                time_start=date(2026, 3, 13),
                time_end=date(2026, 3, 13),
                normalized_query=NormalizedQuery(
                    intent=QueryIntent.TRANSACTION_SEARCH,
                    time_range=TimeRange(start=date(2026, 3, 13), end=date(2026, 3, 13)),
                ),
            ),
            surface=surface,
        )
    )

    assert decision.decision == "continuation"
    assert decision.continuation_type == "drill_down"
    assert decision.drill_down_action == "get_receipt"


@pytest.mark.asyncio
async def test_extraction_step_fresh_query_uses_reasoner_extraction_without_parser_parse() -> None:
    step = ExtractionStep(_FailingLLM())

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="fresh_query",
            extraction=QueryExtractionResult(
                raw_query="show my last transaction",
                result_limit=1,
                result_reference="latest",
            ),
        )

    def _fail_parse(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("fresh query should not call parser.parse")

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.parse = _fail_parse  # type: ignore[method-assign]

    result = await step.run(
        {
            "message": "show my last transaction",
            "language": "en",
            "today": date(2026, 3, 13),
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["flow_state"] == "executing"


@pytest.mark.asyncio
async def test_extraction_step_active_result_aggregate_can_reuse_reasoner_extraction() -> None:
    step = ExtractionStep(_FailingLLM())
    session_contract = QueryExecutionContract(
        intent=QueryIntent.TRANSACTION_LIST,
        time_start=date(2026, 3, 1),
        time_end=date(2026, 3, 13),
        normalized_query=NormalizedQuery(
            intent=QueryIntent.TRANSACTION_LIST,
            time_range=TimeRange(start=date(2026, 3, 1), end=date(2026, 3, 13)),
            filters=Filters(transaction_type="debit"),
        ),
    )

    async def _fake_reason(context: object) -> QuerySemanticDecision:
        del context
        return QuerySemanticDecision(
            decision="continuation",
            continuation_type="aggregate",
            extraction=QueryExtractionResult(
                raw_query="how much did i spend",
                result_limit=None,
                filters=QueryFilters(transaction_type="debit"),
            ),
        )

    def _fail_parse(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("aggregate continuation should not call parser.parse")

    step.reasoner.reason = _fake_reason  # type: ignore[method-assign]
    step.parser.parse = _fail_parse  # type: ignore[method-assign]

    result = await step.run(
        {
            "message": "how much did i spend",
            "language": "en",
            "today": date(2026, 3, 13),
            "query_session": {
                "session_active": True,
                "query_contract": session_contract,
                "query_result": {"items": [QueryResultItem(description="Txn", amount=1000, date=date(2026, 3, 13)).model_dump()]},
                "surface": ResultSurface(type=SurfaceType.LIST, items=[], context={}),
            },
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["flow_state"] == "executing"
