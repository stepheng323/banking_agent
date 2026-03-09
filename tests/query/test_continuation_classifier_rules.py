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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "how much did I spend yesterday",
        "how much did I spend",
        "total spending",
        "how many transactions",
        "sum it up",
        "How much have I spent today",
    ],
)
async def test_aggregate_phrases_hit_guardrail(message: str) -> None:
    classifier = ContinuationClassifier(_FailingLLM())

    continuation_type, data = await classifier.classify(
        message=message,
        has_active_session=True,
        today=date.today().isoformat(),
    )

    assert continuation_type == "aggregate"
    assert data["reason"] == "deterministic_aggregate"
    assert data["confidence"] == 0.95


@pytest.mark.asyncio
async def test_time_delta_shortcut_skips_llm_for_yesterday_followup() -> None:
    classifier = ContinuationClassifier(_FailingLLM())

    continuation_type, data = await classifier.classify(
        message="what about yesterday",
        has_active_session=True,
        today="2026-03-06",
    )

    assert continuation_type == "time_delta"
    assert data["reason"] == "deterministic_time_delta"
    assert data["delta_type"] == "time"
    assert data["time_range"].start.isoformat() == "2026-03-05"
    assert data["time_range"].end.isoformat() == "2026-03-05"


@pytest.mark.asyncio
async def test_recipient_ranking_followup_forces_new_query_override() -> None:
    classifier = ContinuationClassifier(_FailingLLM())

    continuation_type, data = await classifier.classify(
        message="Who did I send money to the most this week",
        has_active_session=True,
        today="2026-03-07",
    )

    assert continuation_type == "new_query"
    assert data["reason"] == "deterministic_recipient_ranking_new_query"
    assert data["is_new_query_override"] is True
    assert data["restates_query"] is True


@pytest.mark.asyncio
async def test_filter_delta_shortcut_skips_llm_for_credit_debit_followups() -> None:
    classifier = ContinuationClassifier(_FailingLLM())

    continuation_type, data = await classifier.classify(
        message="only debits",
        has_active_session=True,
        today="2026-03-06",
    )

    assert continuation_type == "filter_delta"
    assert data["reason"] == "deterministic_tx_type_filter"
    assert data["delta_type"] == "filter"
    assert data["filters"].transaction_type == "debit"

    continuation_type2, data2 = await classifier.classify(
        message="what about credits",
        has_active_session=True,
        today="2026-03-06",
    )

    assert continuation_type2 == "filter_delta"
    assert data2["filters"].transaction_type == "credit"


@pytest.mark.asyncio
async def test_balance_phrase_forces_new_query_override_without_llm() -> None:
    classifier = ContinuationClassifier(_FailingLLM())

    continuation_type, data = await classifier.classify(
        message="check my balance",
        has_active_session=True,
        today="2026-03-06",
    )

    assert continuation_type == "new_query"
    assert data["reason"] == "deterministic_account_balance_new_query"
    assert data["is_new_query_override"] is True
