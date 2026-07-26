from collections import namedtuple
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from banking.transactions.query.models.domain import (
    Aggregation,
    QueryIntent,
    TimeRange,
)
from banking.transactions.query.worker import QueryWorker
from tests.query.factories import make_query_request

WorkerContext = namedtuple("WorkerContext", ["redis", "tracer", "queue", "user_id"])


@pytest.mark.asyncio
async def test_analytics_drill_down_executes():
    llm = MagicMock()
    banking = AsyncMock()
    banking.search_transactions.return_value = []

    # Mock LLM to return classification
    class MockDecision:
        decision = "continuation"
        continuation_type = "drill_down"
        drill_down_action = "answer_fact"
        fact_field = "reference"
        confidence = 1.0
        response_text = ""
        contextual_hint = ""
        drill_down_index = None
        requested_field = "reference"
        delta_type = None
        followup_intent = "none"
        reason = "test reason"

    reasoner = AsyncMock()
    reasoner.reason.return_value = MockDecision()

    worker = QueryWorker(llm=llm, banking_provider=banking)
    worker.extractor.reasoner = reasoner

    # Build active session
    from banking.transactions.query.contracts import SelectionPayload, SurfaceItemView, SurfaceView, SurfaceViewMode

    surface = SurfaceView(
        mode=SurfaceViewMode.DIRECT_ANSWER,
        items=[
            SurfaceItemView(
                id="group_1",
                label="Acme Corp",
                amount=950000,
                count=1,
                payload=SelectionPayload(
                    selection_kind="transaction",
                    entity_type="transaction",
                    entity_id="group_1",
                    label="Acme Corp",
                ),
            )
        ],
    )
    contract = (
        make_query_request(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_range=TimeRange(start=date.today(), end=date.today()),
            aggregation=Aggregation(type="sum", group_by="merchant", sort_by="amount"),
        )
    )

    state = {
        "user_id": "test_user",
        "message": "What the reference number",
        "language": "en",
        "today": date.today(),
        "query_session": {
            "session_active": True,
            "query_request": contract.model_dump(mode="json"),
            "query_result": {
                "surface_view": surface.model_dump(mode="json"),
                "items": [
                    {
                        "id": "group_1",
                        "description": "Acme Corp",
                        "amount": 950000,
                        "date": "2026-06-28",
                        "metadata": {"key": "Acme Corp"},
                    }
                ],
            },
        },
    }

    ctx = WorkerContext(redis=MagicMock(), tracer=MagicMock(), queue=MagicMock(), user_id="test_user")
    res = await worker._run_pipeline(state, ctx)

    print("FLOW STATE:", state.get("flow_state"))
    print("OUTCOME:", res.outcome)
    if "query_request" in state:
        print("NEW INTENT:", state["query_request"].intent)
