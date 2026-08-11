"""Query extraction tests for force-new-query interrupt hint."""

from datetime import date

import pytest

from banking.runtime.results import TransactionOutcome
from banking.transactions.query.models.extraction import QueryExtractionResult
from banking.transactions.query.nodes.extraction import ExtractionStep


class _DummyStructured:
    async def ainvoke(self, prompt: str) -> QueryExtractionResult:
        del prompt
        return QueryExtractionResult(raw_query="fallback")


class _DummyLLM:
    def with_structured_output(self, schema: object) -> _DummyStructured:
        del schema
        return _DummyStructured()


@pytest.mark.asyncio
async def test_force_new_query_bypasses_continuation_classifier() -> None:
    step = ExtractionStep(_DummyLLM())

    calls = {"parse": 0, "continuation": 0}

    async def _fake_parse_new_query(state: dict) -> dict:
        del state
        calls["parse"] += 1
        return {
            "query": {"intent": "transaction_list"},
            "flow_state": "executing",
            "current_page": 0,
            "session_active": True,
            "show_expanded": False,
        }

    async def _fake_handle_continuation(state: dict, session: dict) -> dict:
        del state, session
        calls["continuation"] += 1
        return {"flow_state": "executing"}

    step._parse_new_query = _fake_parse_new_query  # type: ignore[method-assign]
    step._handle_continuation = _fake_handle_continuation  # type: ignore[method-assign]

    result = await step.run(
        {
            "message": "what is my balance",
            "force_new_query": True,
            "language": "en",
            "today": date.today(),
            "query_session": {"schema_version": 3, "session_active": True},
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert calls == {"parse": 1, "continuation": 0}
