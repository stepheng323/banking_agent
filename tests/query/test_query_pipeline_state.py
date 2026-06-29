from datetime import date

import pytest

from banking.runtime.results import TransactionOutcome, TransactionResult
from banking.transactions.query.models.domain import QueryExecutionContract, QueryIntent, QueryIR, TimeRange
from banking.transactions.query.pipeline import QueryPipeline, QueryStep


class _NewContractStep(QueryStep):
    async def run(self, state: dict, worker_context: object = None) -> TransactionResult:
        del state, worker_context
        contract = QueryExecutionContract.from_query_ir(
            QueryIR(
                intent=QueryIntent.ANALYTICS_SUMMARY,
                time_range=TimeRange(start=date(2026, 6, 1), end=date(2026, 6, 27)),
            )
        )
        return TransactionResult(outcome=TransactionOutcome.OK, patch={"query_contract": contract})


@pytest.mark.asyncio
async def test_pipeline_clears_stale_selection_when_new_query_contract_is_patched() -> None:
    result = await QueryPipeline([_NewContractStep()]).run(
        {
            "selected_item_index": 0,
            "selected_item_id": "old",
            "selected_payload": {"entity_id": "old"},
            "fact_field": "reference",
            "drill_down_action": "answer_fact",
        }
    )

    assert result.patch is not None
    assert "query_contract" in result.patch
    assert "selected_item_index" not in result.patch
    assert "selected_item_id" not in result.patch
    assert "selected_payload" not in result.patch
    assert "fact_field" not in result.patch
    assert "drill_down_action" not in result.patch
