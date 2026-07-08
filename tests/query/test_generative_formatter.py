from __future__ import annotations

from typing import Any

import pytest

from banking.runtime.results import TransactionOutcome
from banking.transactions.query.models.domain import QueryResult
from banking.transactions.query.nodes.generative_formatter import GenerativeFormattingStep


class _FakeChain:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def ainvoke(self, payload: dict[str, Any]) -> str:
        self.calls.append(payload)
        return self.response


def _step_with_response(response: str) -> GenerativeFormattingStep:
    step = GenerativeFormattingStep(object())  # type: ignore[arg-type]
    step.chain = _FakeChain(response)
    return step


@pytest.mark.asyncio
async def test_generative_formatter_accepts_fact_preserving_rewrite() -> None:
    step = _step_with_response("You spent ₦1,000 this month across 2 transactions.")
    result = await step.run(
        {
            "flow_state": "complete",
            "message": "How much did I spend this month?",
            "language": "en",
            "query_result": QueryResult(
                summary_text="You spent ₦1,000 this month, across 2 transactions.",
                items=[],
            ),
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.response == "You spent ₦1,000 this month across 2 transactions."


@pytest.mark.asyncio
async def test_generative_formatter_rejects_rewrite_that_changes_financial_facts() -> None:
    step = _step_with_response("You spent ₦2,000 this month across 2 transactions.")
    result = await step.run(
        {
            "flow_state": "complete",
            "message": "How much did I spend this month?",
            "language": "en",
            "query_result": QueryResult(
                summary_text="You spent ₦1,000 this month, across 2 transactions.",
                items=[],
            ),
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.response == "You spent ₦1,000 this month, across 2 transactions."


@pytest.mark.asyncio
async def test_generative_formatter_accepts_equivalent_date_day_formatting() -> None:
    step = _step_with_response("You got money from Acme Corp on July 5, 2026.")
    result = await step.run(
        {
            "flow_state": "complete",
            "message": "When was that?",
            "language": "en",
            "query_result": QueryResult(
                summary_text="You got money from Acme Corp on July 05, 2026.",
                items=[],
            ),
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.response == "You got money from Acme Corp on July 5, 2026."


@pytest.mark.asyncio
async def test_generative_formatter_rejects_rewrite_that_changes_reference_or_masked_account() -> None:
    step = _step_with_response("The reference is txn_999, and it was from GTBank · ···0002.")
    result = await step.run(
        {
            "flow_state": "complete",
            "message": "What is the reference?",
            "language": "en",
            "query_result": QueryResult(
                summary_text="The reference is txn_002, and it was from GTBank · ···0001.",
                items=[],
            ),
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.response == "The reference is txn_002, and it was from GTBank · ···0001."


@pytest.mark.asyncio
async def test_generative_formatter_rejects_rewrite_that_drops_structured_rows() -> None:
    system_response = (
        "Here is your Money came in by account this month:\n\n"
        "₦1,200,000 — GTBank (54%, 2 txns)\n"
        "₦1,000,000 — First Bank (46%, 2 txns)\n\n"
        "Total: ₦2,200,000"
    )
    step = _step_with_response("₦2,200,000 came into your accounts this month, across 4 transactions.")
    result = await step.run(
        {
            "flow_state": "complete",
            "message": "Break down by account",
            "language": "en",
            "query_result": QueryResult(
                summary_text=system_response,
                items=[],
            ),
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.response == system_response
