from datetime import date

import pytest

from apps.core.src.agent.graphs.query.models import ExtractionIntent, QueryExtractionResult
from apps.core.src.agent.graphs.query.services.parser import QueryParser
from shared.i18n import render_message


class _DummyStructured:
    def __init__(self, extraction: QueryExtractionResult) -> None:
        self._extraction = extraction

    async def ainvoke(self, prompt: str) -> QueryExtractionResult:
        del prompt
        return self._extraction.model_copy(deep=True)


class _DummyLLM:
    def __init__(self, extraction: QueryExtractionResult) -> None:
        self._extraction = extraction

    def with_structured_output(self, schema: object) -> _DummyStructured:
        del schema
        return _DummyStructured(self._extraction)


@pytest.mark.asyncio
async def test_time_comparison_without_explicit_time_returns_needs_input() -> None:
    extraction = QueryExtractionResult(intent=ExtractionIntent.TRANSACTION_LIST)
    parser = QueryParser(_DummyLLM(extraction))

    result = await parser.parse(
        "compare my spending",
        today=date(2026, 3, 7),
        language="en",
    )

    assert result.outcome.value == "NEEDS_INPUT"
    assert result.resolver_message == render_message("query.time_comparison.prompt_specify_period", "en")
    assert result.query_contract is None
