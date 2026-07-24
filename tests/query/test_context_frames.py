from datetime import date

from banking.transactions.query.context_frames import build_reasoner_context_from_frames
from banking.transactions.query.models.domain import QueryIntent, TimeRange
from tests.query.factories import make_query_request


def test_context_frame_with_null_items_restores_an_empty_query_surface() -> None:
    query_request = (
        make_query_request(
            intent=QueryIntent.TRANSACTION_LIST,
            time_range=TimeRange(start=date(2026, 7, 1), end=date(2026, 7, 13), granularity="month"),
        )
    )
    frame = {
        "frame_id": "query-frame-1",
        "created_at_ts": 1.0,
        "items": None,
        "metadata": {
            "source": "query",
            "query_request": query_request.model_dump(mode="json"),
            "surface_mode": "transaction_list",
            "summary_text": "No transactions found.",
        },
    }

    context = build_reasoner_context_from_frames(frame, [frame])

    assert context["session_active"] is True
    assert context["query_result"]["items"] == []
    assert context["query_result"]["surface_view"]["items"] == []
    assert len(context["query_frames"]) == 1
