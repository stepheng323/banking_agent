from datetime import date

import pytest

from apps.core.src.agent.graphs.query.models import ResultSurface, SurfaceType
from apps.core.src.agent.graphs.query.services.continuity import ContinuationClassification, ContinuationClassifier


class _DummyStructured:
    def __init__(self, result: ContinuationClassification):
        self._result = result

    async def ainvoke(self, prompt: str) -> ContinuationClassification:
        del prompt
        return self._result


class _DummyLLM:
    def __init__(self, result: ContinuationClassification):
        self._result = result

    def with_structured_output(self, schema: object) -> _DummyStructured:
        del schema
        return _DummyStructured(self._result)


class _FailingLLM:
    def with_structured_output(self, schema: object) -> "_FailingStructured":
        del schema
        return _FailingStructured()


class _FailingStructured:
    async def ainvoke(self, prompt: str) -> ContinuationClassification:
        del prompt
        raise AssertionError("LLM should not be called for deterministic guardrail path")


@pytest.mark.asyncio
async def test_summary_show_my_transactions_is_expand_guardrail() -> None:
    classifier = ContinuationClassifier(_FailingLLM())
    surface = ResultSurface(type=SurfaceType.SUMMARY, items=[], context={"view": "summary"})

    continuation_type, data = await classifier.classify(
        message="show my transactions",
        has_active_session=True,
        today=date.today().isoformat(),
        surface=surface,
    )

    assert continuation_type == "expand"
    assert data["reason"] == "deterministic_expand"


@pytest.mark.asyncio
async def test_repeated_send_phrase_maps_to_retransfer_action() -> None:
    llm_result = ContinuationClassification(
        continuation_type="new_query",
        confidence=0.2,
        reason="fallback",
    )
    classifier = ContinuationClassifier(_DummyLLM(llm_result))
    surface = ResultSurface(type=SurfaceType.SINGLE_ITEM, items=[], context={"type": "single_transaction"})

    continuation_type, data = await classifier.classify(
        message="resend it",
        has_active_session=True,
        today=date.today().isoformat(),
        surface=surface,
    )

    assert continuation_type == "drill_down"
    assert data["drill_down_action"] == "re_transfer"


@pytest.mark.asyncio
async def test_restated_full_query_remains_new_query_override() -> None:
    llm_result = ContinuationClassification(
        continuation_type="new_query",
        confidence=0.97,
        reason="full_restate",
        is_new_query_override=True,
        restates_query=True,
    )
    classifier = ContinuationClassifier(_DummyLLM(llm_result))
    surface = ResultSurface(type=SurfaceType.LIST, items=[], context={"count": 5})

    continuation_type, data = await classifier.classify(
        message="show my last 5 transfers",
        has_active_session=True,
        today=date.today().isoformat(),
        surface=surface,
    )

    assert continuation_type == "new_query"
    assert data["is_new_query_override"] is True
    assert data["restates_query"] is True
