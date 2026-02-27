from typing import Any
from unittest.mock import AsyncMock

import pytest

from apps.core.src.lambda_handlers.messaging_worker_handler import MessagingWorkerLambdaHandler
from apps.core.src.lambda_handlers.transaction_worker_handler import TransactionWorkerLambdaHandler


def _queue_arn(queue_name: str) -> str:
    return f"arn:aws:sqs:eu-west-1:123456789012:banking-agent-{queue_name}-dev"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "queue_name",
    [
        "banking-transactions",
        "banking-funding",
        "banking-payouts",
        "banking-refunds",
    ],
)
async def test_transaction_worker_routes_by_queue(queue_name: str) -> None:
    transaction = type("TransactionConsumer", (), {"process_transaction": AsyncMock()})()
    funding = type("FundingConsumer", (), {"process_job": AsyncMock()})()
    payout = type("PayoutConsumer", (), {"process_job": AsyncMock()})()
    refund = type("RefundConsumer", (), {"process_job": AsyncMock()})()
    deps: tuple[Any, ...] = (transaction, funding, payout, refund)

    handler = TransactionWorkerLambdaHandler(name="test_transaction_worker", dependecy_loader=lambda: deps)
    event = {"Records": [{"messageId": "m1", "eventSourceARN": _queue_arn(queue_name), "body": "{}"}]}

    result = await handler.process_event(event)

    assert result["failed"] == 0
    expected_target = {
        "banking-transactions": transaction.process_transaction,
        "banking-funding": funding.process_job,
        "banking-payouts": payout.process_job,
        "banking-refunds": refund.process_job,
    }[queue_name]
    expected_target.assert_awaited_once_with({})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "queue_name",
    [
        "banking-outbox",
        "banking-actionable-messages",
        "banking-receipt-jobs",
    ],
)
async def test_messaging_worker_routes_by_queue(queue_name: str) -> None:
    outbox = type("OutboxConsumer", (), {"process_job": AsyncMock()})()
    actionable = type("ActionableConsumer", (), {"process_job": AsyncMock()})()
    receipt = type("ReceiptConsumer", (), {"process_job": AsyncMock()})()
    deps: tuple[Any, ...] = (outbox, actionable, receipt)

    handler = MessagingWorkerLambdaHandler(name="test_messaging_worker", dependecy_loader=lambda: deps)
    event = {"Records": [{"messageId": "m1", "eventSourceARN": _queue_arn(queue_name), "body": "{}"}]}

    result = await handler.process_event(event)

    assert result["failed"] == 0
    expected_target = {
        "banking-outbox": outbox.process_job,
        "banking-actionable-messages": actionable.process_job,
        "banking-receipt-jobs": receipt.process_job,
    }[queue_name]
    expected_target.assert_awaited_once_with({})


@pytest.mark.asyncio
async def test_worker_unknown_queue_returns_batch_failure() -> None:
    deps: tuple[Any, ...] = (object(), object(), object(), object())
    handler = TransactionWorkerLambdaHandler(name="test_transaction_worker", dependecy_loader=lambda: deps)
    event = {"Records": [{"messageId": "m1", "eventSourceARN": _queue_arn("banking-unknown"), "body": "{}"}]}

    result = await handler.process_event(event)

    assert result["failed"] == 1
    assert result["batchItemFailures"] == [{"itemIdentifier": "m1"}]
