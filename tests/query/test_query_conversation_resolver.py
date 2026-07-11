from datetime import date

import pytest

from banking.runtime.results import TransactionOutcome
from banking.transactions.query.actions import handle_drill_down
from banking.transactions.query.contracts import SelectionPayload, SurfaceItemView, SurfaceView, SurfaceViewMode
from banking.transactions.query.models.domain import (
    Filters,
    QueryExecutionContract,
    QueryFrame,
    QueryFrameFacts,
    QueryIntent,
    QueryResult,
    QueryResultItem,
)
from banking.transactions.query.services.conversation.resolver import build_query_conversation_updates
from banking.transactions.query.services.reasoning.models import QuerySemanticDecision


def _payload(entity_id: str, label: str) -> SelectionPayload:
    return SelectionPayload(
        selection_kind="transaction",
        entity_type="transaction",
        entity_id=entity_id,
        label=label,
    )


def _surface() -> SurfaceView:
    return SurfaceView(
        mode=SurfaceViewMode.TRANSACTION_LIST,
        items=[
            SurfaceItemView(
                id="tx-mum",
                label="Transfer to Mum",
                amount=50000,
                payload=_payload("tx-mum", "Transfer to Mum"),
                metadata={"bank_name": "Zenith Bank", "transaction_type": "debit", "recipient_name": "Mum"},
            ),
            SurfaceItemView(
                id="tx-adebayo",
                label="Transfer to Adebayo James",
                amount=25000,
                payload=_payload("tx-adebayo", "Transfer to Adebayo James"),
                metadata={"bank_name": "Zenith Bank", "transaction_type": "debit", "recipient_name": "Adebayo James"},
            ),
        ],
    )


def _query_result() -> QueryResult:
    return QueryResult(
        summary_text="recent transactions",
        items=[
            QueryResultItem(
                id="tx-mum",
                description="Transfer to Mum",
                amount=50000,
                date=date(2026, 5, 7),
                metadata={"bank_name": "Zenith Bank", "recipient_name": "Mum"},
            ),
            QueryResultItem(
                id="tx-adebayo",
                description="Transfer to Adebayo James",
                amount=25000,
                date=date(2026, 5, 6),
                metadata={"bank_name": "Zenith Bank", "recipient_name": "Adebayo James"},
            ),
        ],
        surface_view=_surface(),
    )


def _contract() -> QueryExecutionContract:
    return QueryExecutionContract(
        intent=QueryIntent.TRANSACTION_LIST,
        time_start=date(2026, 4, 10),
        time_end=date(2026, 5, 10),
    )


def _frame_with_items(frame_id: str, items: list[dict[str, object]]) -> QueryFrame:
    return QueryFrame(
        frame_id=frame_id,
        turn_index=1,
        query_contract=_contract(),
        summary_text="Transactions",
        surface_type=SurfaceViewMode.TRANSACTION_LIST,
        visible_items=items,
        facts=QueryFrameFacts(metric_kind="transactions", count=len(items)),
    )


def test_query_conversation_resolver_selects_visible_amount_reference() -> None:
    updates = build_query_conversation_updates(
        surface_view=_surface(),
        query_result=_query_result(),
        decision=QuerySemanticDecision(
            decision="continuation",
            confidence=0.95,
            continuation_type="drill_down",
            drill_down_action="view_details",
            target_amount=25000,
        ),
        text="show the 25k one",
    )

    assert updates is not None
    assert updates["selected_item_id"] == "tx-adebayo"
    assert updates["selected_item_index"] == 1
    assert updates["selected_payload"].entity_id == "tx-adebayo"


def test_query_conversation_resolver_prefers_visible_amount_over_bad_reasoner_index() -> None:
    updates = build_query_conversation_updates(
        surface_view=_surface(),
        query_result=_query_result(),
        decision=QuerySemanticDecision(
            decision="continuation",
            confidence=0.95,
            continuation_type="drill_down",
            drill_down_action="view_details",
            target_index=6,
        ),
        text="show the 25k one",
    )

    assert updates is not None
    assert updates["selected_item_id"] == "tx-adebayo"
    assert updates["selected_item_index"] == 1


def test_query_conversation_resolver_target_overrides_misclassified_pagination() -> None:
    updates = build_query_conversation_updates(
        surface_view=_surface(),
        query_result=_query_result(),
        decision=QuerySemanticDecision(
            decision="continuation",
            confidence=0.95,
            continuation_type="show_more",
            followup_intent="continue_pagination",
            target_amount=50000,
        ),
        text="show the 50k one",
    )

    assert updates is not None
    assert updates["continuation_type"] == "drill_down"
    assert updates["selected_item_id"] == "tx-mum"
    assert "current_page" not in updates


def test_query_conversation_resolver_rejects_missing_amount_without_fallback() -> None:
    updates = build_query_conversation_updates(
        surface_view=_surface(),
        query_result=_query_result(),
        decision=QuerySemanticDecision(
            decision="continuation",
            confidence=0.95,
            continuation_type="drill_down",
            drill_down_action="view_details",
        ),
        text="show the details of the 20k one",
    )

    assert updates is not None
    assert updates["response"] == "I don't see ₦20,000 in the transactions I showed."
    assert "selected_item_index" not in updates
    assert "current_page" not in updates


def test_query_conversation_resolver_does_not_pick_nearest_amount() -> None:
    updates = build_query_conversation_updates(
        surface_view=_surface(),
        query_result=_query_result(),
        decision=QuerySemanticDecision(
            decision="continuation",
            confidence=0.95,
            continuation_type="drill_down",
            drill_down_action="view_details",
        ),
        text="what is the 20k one for",
    )

    assert updates is not None
    assert updates["response"] == "I don't see ₦20,000 in the transactions I showed."
    assert "selected_item_id" not in updates


def test_query_conversation_resolver_accepts_safe_fuzzy_visible_label() -> None:
    surface = SurfaceView(
        mode=SurfaceViewMode.TRANSACTION_LIST,
        items=[
            SurfaceItemView(
                id="tx-glovo",
                label="GLOVO Food Delivery",
                amount=4500,
                payload=_payload("tx-glovo", "GLOVO Food Delivery"),
                metadata={"bank_name": "First Bank", "transaction_type": "debit", "counterparty": "Glovo"},
            )
        ],
    )

    updates = build_query_conversation_updates(
        surface_view=surface,
        query_result=None,
        decision=QuerySemanticDecision(
            decision="continuation",
            confidence=0.95,
            continuation_type="drill_down",
            drill_down_action="view_details",
            target_text="glove",
        ),
        text="show the glove one",
    )

    assert updates is not None
    assert updates["selected_item_id"] == "tx-glovo"


def test_query_conversation_resolver_uses_recent_list_frame_after_detail() -> None:
    detail_surface = SurfaceView(
        mode=SurfaceViewMode.DIRECT_ANSWER,
        items=[
            SurfaceItemView(
                id="tx-mum",
                label="Transfer to Mum",
                amount=50000,
                payload=_payload("tx-mum", "Transfer to Mum"),
                metadata={"bank_name": "Zenith Bank", "transaction_type": "debit", "recipient_name": "Mum"},
            )
        ],
    )
    prior_frame = _frame_with_items(
        "qf_1",
        [
            {
                "id": "tx-mum",
                "label": "Transfer to Mum",
                "amount": 50000.0,
                "bank": "Zenith Bank",
                "counterparty": "Mum",
                "direction": "debit",
                "date": "2026-05-07",
                "page_position": 1,
            },
            {
                "id": "tx-dad",
                "label": "Transfer to Dad",
                "amount": 30000.0,
                "bank": "First Bank",
                "counterparty": "Dad",
                "direction": "debit",
                "date": "2026-05-06",
                "page_position": 2,
            },
            {
                "id": "tx-adebayo",
                "label": "Transfer to Adebayo James",
                "amount": 25000.0,
                "bank": "Zenith Bank",
                "counterparty": "Adebayo James",
                "direction": "debit",
                "date": "2026-05-06",
                "page_position": 3,
            },
        ],
    )

    updates = build_query_conversation_updates(
        surface_view=detail_surface,
        query_result=QueryResult(summary_text="detail", items=[]),
        query_frames=[prior_frame],
        decision=QuerySemanticDecision(
            decision="continuation",
            confidence=0.95,
            continuation_type="drill_down",
            drill_down_action="view_details",
            target_index=3,
        ),
        text="now show the 3rd transaction",
    )

    assert updates is not None
    assert updates["selected_item_id"] == "tx-adebayo"
    assert updates["selected_frame_id"] == "qf_1"
    assert updates["selected_query_item"].description == "Transfer to Adebayo James"


def test_query_conversation_resolver_does_not_search_old_frames_without_prior_reference() -> None:
    prior_frame = _frame_with_items(
        "qf_1",
        [
            {
                "id": "tx-old-20k",
                "label": "Transfer to Old Recipient",
                "amount": 20000.0,
                "bank": "Zenith Bank",
                "counterparty": "Old Recipient",
                "direction": "debit",
                "date": "2026-05-01",
                "page_position": 1,
            }
        ],
    )

    updates = build_query_conversation_updates(
        surface_view=_surface(),
        query_result=_query_result(),
        query_frames=[prior_frame],
        decision=QuerySemanticDecision(
            decision="continuation",
            confidence=0.95,
            continuation_type="drill_down",
            drill_down_action="view_details",
        ),
        text="show the 20k one",
    )

    assert updates is not None
    assert updates["response"] == "I don't see ₦20,000 in the transactions I showed."
    assert "selected_item_id" not in updates


def test_query_conversation_resolver_leaves_filter_delta_to_query_refinement() -> None:
    updates = build_query_conversation_updates(
        surface_view=_surface(),
        query_result=_query_result(),
        decision=QuerySemanticDecision(
            decision="continuation",
            confidence=0.95,
            continuation_type="filter_delta",
            followup_intent="refine_existing",
            filters=Filters(transaction_type="debit"),
        ),
        text="what about debits",
    )

    assert updates is None


def test_query_conversation_resolver_leaves_coverage_to_coverage_handler() -> None:
    updates = build_query_conversation_updates(
        surface_view=_surface(),
        query_result=_query_result(),
        decision=QuerySemanticDecision(
            decision="continuation",
            confidence=0.95,
            continuation_type="coverage",
            followup_intent="none",
            target_text="list",
        ),
        text="is this all?",
    )

    assert updates is None


def test_query_conversation_resolver_maps_requested_field_to_answer_fact() -> None:
    updates = build_query_conversation_updates(
        surface_view=_surface(),
        query_result=_query_result(),
        decision=QuerySemanticDecision(
            decision="continuation",
            confidence=0.95,
            continuation_type="drill_down",
            requested_field="bank",
            target_index=2,
        ),
        text="what bank was the second one",
    )

    assert updates is not None
    assert updates["selected_item_id"] == "tx-adebayo"
    assert updates["drill_down_action"] == "answer_fact"
    assert updates["fact_field"] == "bank"


def test_query_conversation_resolver_maps_new_safe_requested_fields() -> None:
    updates = build_query_conversation_updates(
        surface_view=_surface(),
        query_result=_query_result(),
        decision=QuerySemanticDecision(
            decision="continuation",
            confidence=0.95,
            continuation_type="drill_down",
            requested_field="description",
            target_index=1,
        ),
        text="what is the first one for",
    )

    assert updates is not None
    assert updates["drill_down_action"] == "answer_fact"
    assert updates["fact_field"] == "description"


@pytest.mark.asyncio
async def test_query_drill_down_does_not_fallback_to_item_zero_for_bad_selection_payload() -> None:
    query_result = _query_result()

    result = await handle_drill_down(
        {
            "language": "en",
            "query_result": query_result,
            "drill_down_action": "view_details",
            "selected_payload": _payload("missing-tx", "Missing transaction").model_dump(),
        }
    )

    assert result.outcome == TransactionOutcome.FAILED
    assert "no" in (result.response or "").lower()


@pytest.mark.asyncio
async def test_query_drill_down_retains_parent_list_pagination_for_completeness_followup() -> None:
    query_result = _query_result().model_copy(update={"has_more": True, "query_contract": _contract()})

    result = await handle_drill_down(
        {
            "language": "en",
            "query_result": query_result,
            "selected_item_index": 1,
            "drill_down_action": "view_details",
        }
    )

    assert result.outcome == TransactionOutcome.OK
    detail_result = result.patch["query_result"]
    assert detail_result.has_more is True
    assert detail_result.summary_text == "recent transactions"
    assert detail_result.surface_view is not None
    assert detail_result.surface_view.context["parent_visible_count"] == 2
