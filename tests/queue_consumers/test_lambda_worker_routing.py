from typing import Any
from unittest.mock import AsyncMock

import pytest

from apps.receipt.lambda_handler import ReceiptWorkerLambdaHandler
from apps.transaction.lambda_handler import TransactionWorkerLambdaHandler


def _queue_arn(queue_name: str) -> str:
    return f"arn:aws:sqs:us-east-1:123456789012:banking-agent-{queue_name}-dev"


def _sns_record(domain: str, queue: str = "banking-transactions") -> dict[str, Any]:
    """Build a minimal SQS record with SNS-forwarded domain message attribute."""
    return {
        "messageId": "m1",
        "eventSourceARN": _queue_arn(queue),
        "body": "{}",
        "messageAttributes": {
            "domain": {"stringValue": domain, "dataType": "String"},
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["transaction", "funding", "payout", "refund"])
async def test_transaction_worker_routes_by_domain(domain: str) -> None:
    transaction = type("TransactionConsumer", (), {"process_transaction": AsyncMock()})()
    funding = type("FundingConsumer", (), {"process_job": AsyncMock()})()
    payout = type("PayoutConsumer", (), {"process_job": AsyncMock()})()
    refund = type("RefundConsumer", (), {"process_job": AsyncMock()})()
    deps: tuple[Any, ...] = (transaction, funding, payout, refund)

    handler = TransactionWorkerLambdaHandler(name="test_transaction_worker", dependency_loader=lambda: deps)
    event = {"Records": [_sns_record(domain)]}

    result = await handler.process_event(event)

    assert result["failed"] == 0
    expected_target = {
        "transaction": transaction.process_transaction,
        "funding": funding.process_job,
        "payout": payout.process_job,
        "refund": refund.process_job,
    }[domain]
    expected_target.assert_awaited_once_with({})


@pytest.mark.asyncio
async def test_receipt_worker_routes_by_domain() -> None:
    receipt = type("ReceiptConsumer", (), {"process_job": AsyncMock()})()
    notification = type("NotificationConsumer", (), {"process_job": AsyncMock()})()
    handler = ReceiptWorkerLambdaHandler(name="test_receipt_worker", dependency_loader=lambda: (receipt, notification))
    event = {"Records": [_sns_record("receipt", queue="banking-receipts")]}

    result = await handler.process_event(event)

    assert result["failed"] == 0
    receipt.process_job.assert_awaited_once_with({})
    notification.process_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_receipt_worker_routes_notification_domain() -> None:
    receipt = type("ReceiptConsumer", (), {"process_job": AsyncMock()})()
    notification = type("NotificationConsumer", (), {"process_job": AsyncMock()})()
    handler = ReceiptWorkerLambdaHandler(name="test_receipt_worker", dependency_loader=lambda: (receipt, notification))
    event = {"Records": [_sns_record("notification", queue="banking-receipts")]}

    result = await handler.process_event(event)

    assert result["failed"] == 0
    receipt.process_job.assert_not_awaited()
    notification.process_job.assert_awaited_once_with({})


@pytest.mark.asyncio
async def test_worker_unknown_domain_returns_batch_failure() -> None:
    deps: tuple[Any, ...] = (object(), object(), object(), object())
    handler = TransactionWorkerLambdaHandler(name="test_transaction_worker", dependency_loader=lambda: deps)
    event = {"Records": [_sns_record("unknown")]}

    result = await handler.process_event(event)

    assert result["failed"] == 1
    assert result["batchItemFailures"] == [{"itemIdentifier": "m1"}]
