from datetime import date

import pytest

from apps.chat.src.agent.graphs.query.actions import handle_drill_down
from apps.chat.src.agent.graphs.query.models import QueryResult, QueryResultItem
from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome
from apps.chat.src.agent.shared.query_contracts import SelectionPayload, SurfaceViewMode


@pytest.mark.asyncio
async def test_retransfer_builds_handoff_payload_for_transfer_item() -> None:
    item = QueryResultItem(
        id="txn-1",
        description="Transfer to Tolu",
        amount=5000.0,
        date=date.today(),
        metadata={
            "transaction_type": "transfer",
            "recipient_name": "Tolu",
            "recipient_account_number": "8162511023",
            "recipient_bank_name": "Opay",
            "recipient_bank_code": "999992",
            "transaction_id": "prov-1",
        },
    )
    query_result = QueryResult(summary_text="single", items=[item], context_key="ctx-1")

    result = await handle_drill_down(
        {
            "language": "en",
            "query_result": query_result,
            "drill_down_action": "re_transfer",
            "selected_item_index": 0,
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["session_active"] is False
    assert "query_transfer_handoff" in result.patch
    handoff = result.patch["query_transfer_handoff"]
    assert handoff["action"] == "send_money"
    assert handoff["amount"] == 5000.0
    assert handoff["recipient_name"] == "Tolu"
    assert handoff["recipient_account"] == "8162511023"
    assert handoff["recipient_bank_name"] == "Opay"
    assert handoff["recipient_bank_code"] == "999992"


@pytest.mark.asyncio
async def test_retransfer_rejects_non_transfer_item() -> None:
    item = QueryResultItem(
        id="txn-2",
        description="Airtime purchase",
        amount=1000.0,
        date=date.today(),
        metadata={"transaction_type": "airtime"},
    )
    query_result = QueryResult(summary_text="single", items=[item], context_key="ctx-2")

    result = await handle_drill_down(
        {
            "language": "en",
            "query_result": query_result,
            "drill_down_action": "re_transfer",
            "selected_item_index": 0,
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["session_active"] is True
    assert "query_transfer_handoff" not in result.patch
    assert "only resend transfer" in (result.response or "").lower()


@pytest.mark.asyncio
async def test_answer_fact_returns_status_from_selected_item() -> None:
    item = QueryResultItem(
        id="txn-3",
        description="Payment to Mum",
        amount=10000.0,
        date=date(2026, 3, 13),
        metadata={"status": "processing", "bank_name": "Zenith Bank", "recipient_name": "Mum"},
    )
    query_result = QueryResult(summary_text="single", items=[item], context_key="ctx-3")

    result = await handle_drill_down(
        {
            "language": "en",
            "query_result": query_result,
            "drill_down_action": "answer_fact",
            "fact_field": "status",
            "selected_item_index": 0,
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["session_active"] is True
    assert "Status:" in (result.response or "")


@pytest.mark.asyncio
async def test_answer_fact_returns_recipient_from_selected_item() -> None:
    item = QueryResultItem(
        id="txn-4",
        description="Payment to Mum",
        amount=10000.0,
        date=date(2026, 3, 13),
        metadata={"recipient_name": "Mum", "bank_name": "Zenith Bank"},
    )
    query_result = QueryResult(summary_text="single", items=[item], context_key="ctx-4")

    result = await handle_drill_down(
        {
            "language": "en",
            "query_result": query_result,
            "drill_down_action": "answer_fact",
            "fact_field": "recipient",
            "selected_item_index": 0,
        }
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["session_active"] is True
    assert "Mum" in (result.response or "")


@pytest.mark.asyncio
async def test_view_details_updates_query_result_to_detail_surface() -> None:
    item = QueryResultItem(
        id="txn-detail",
        description="Transfer to Mum",
        amount=50000.0,
        date=date(2026, 5, 7),
        metadata={"bank_name": "Zenith Bank", "type": "debit", "recipient_name": "Mum"},
    )
    query_result = QueryResult(summary_text="list", items=[item], context_key="ctx-detail")

    result = await handle_drill_down(
        {
            "language": "en",
            "query_result": query_result,
            "drill_down_action": "view_details",
            "selected_item_index": 0,
        }
    )

    assert result.outcome == TransactionOutcome.OK
    detail_result = result.patch["query_result"]
    assert detail_result.surface_view.mode == SurfaceViewMode.DIRECT_ANSWER
    assert detail_result.items == [item]
    assert result.patch["selected_item_id"] == "txn-detail"


@pytest.mark.asyncio
async def test_retransfer_uses_selected_payload_before_fallback_index() -> None:
    first_item = QueryResultItem(
        id="txn-5",
        description="Transfer to Cowrywise",
        amount=50000.0,
        date=date.today(),
        metadata={
            "transaction_type": "transfer",
            "recipient_name": "Cowrywise",
            "recipient_account_number": "1111111111",
            "recipient_bank_name": "Sterling",
        },
    )
    second_item = QueryResultItem(
        id="txn-6",
        description="Transfer to Tolu",
        amount=5000.0,
        date=date.today(),
        metadata={
            "transaction_type": "transfer",
            "recipient_name": "Tolu",
            "recipient_account_number": "8162511023",
            "recipient_bank_name": "Opay",
            "recipient_bank_code": "999992",
        },
    )
    query_result = QueryResult(summary_text="single", items=[first_item, second_item], context_key="ctx-5")

    result = await handle_drill_down(
        {
            "language": "en",
            "query_result": query_result,
            "drill_down_action": "re_transfer",
            "selected_item_index": 0,
            "selected_payload": SelectionPayload(
                selection_kind="transaction",
                entity_type="transaction",
                entity_id="txn-6",
                label="Tolu",
            ).model_dump(),
        }
    )

    assert result.outcome == TransactionOutcome.OK
    handoff = result.patch["query_transfer_handoff"]
    assert handoff["recipient_name"] == "Tolu"
    assert handoff["recipient_account"] == "8162511023"
