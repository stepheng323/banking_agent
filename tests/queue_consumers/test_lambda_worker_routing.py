from typing import Any
from unittest.mock import AsyncMock

import pytest

from apps.receipt.lambda_handler import ReceiptWorkerLambdaHandler
from apps.transaction.dependencies import TransactionWorkerConsumers
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
@pytest.mark.parametrize(
    "domain",
    [
        "transaction",
        "transaction_debit",
        "transaction_debit_reconcile",
        "transaction_debit_refund",
        "transaction_debit_refund_reconcile",
        "direct_transfer_reconcile",
        "funding",
        "funding_reconcile",
        "bill_fulfill",
        "bill_reconcile",
        "payout",
        "payout_reconcile",
        "refund",
        "refund_reconcile",
        "ledger_posting_reconcile",
        "ledger_exposure_reconcile",
    ],
)
async def test_transaction_worker_routes_by_domain(domain: str) -> None:
    transaction = type("TransactionConsumer", (), {"process_transaction": AsyncMock()})()
    transaction_debit = type("TransactionDebitConsumer", (), {"process_job": AsyncMock()})()
    transaction_debit_reconcile = type("TransactionDebitReconciliationConsumer", (), {"process_job": AsyncMock()})()
    transaction_debit_refund = type("TransactionDebitRefundConsumer", (), {"process_job": AsyncMock()})()
    transaction_debit_refund_reconcile = type(
        "TransactionDebitRefundReconciliationConsumer", (), {"process_job": AsyncMock()}
    )()
    direct_transfer_reconcile = type("DirectTransferReconciliationConsumer", (), {"process_job": AsyncMock()})()
    funding = type("FundingConsumer", (), {"process_job": AsyncMock()})()
    funding_reconcile = type("FundingReconciliationConsumer", (), {"process_job": AsyncMock()})()
    bill_fulfill = type("BillFulfillmentConsumer", (), {"process_job": AsyncMock()})()
    bill_reconcile = type("BillReconciliationConsumer", (), {"process_job": AsyncMock()})()
    payout = type("PayoutConsumer", (), {"process_job": AsyncMock()})()
    payout_reconcile = type("PayoutReconciliationConsumer", (), {"process_job": AsyncMock()})()
    refund = type("RefundConsumer", (), {"process_job": AsyncMock()})()
    refund_reconcile = type("RefundReconciliationConsumer", (), {"process_job": AsyncMock()})()
    ledger_posting_reconcile = type("LedgerPostingReconciliationConsumer", (), {"process_job": AsyncMock()})()
    ledger_exposure_reconcile = type("LedgerExposureReconciliationConsumer", (), {"process_job": AsyncMock()})()
    deps = TransactionWorkerConsumers(
        transaction=transaction,
        transaction_debit=transaction_debit,
        transaction_debit_reconciliation=transaction_debit_reconcile,
        transaction_debit_refund=transaction_debit_refund,
        transaction_debit_refund_reconciliation=transaction_debit_refund_reconcile,
        direct_transfer_reconciliation=direct_transfer_reconcile,
        funding=funding,
        bill_fulfillment=bill_fulfill,
        bill_reconciliation=bill_reconcile,
        payout=payout,
        payout_reconciliation=payout_reconcile,
        refund=refund,
        funding_reconciliation=funding_reconcile,
        refund_reconciliation=refund_reconcile,
        ledger_posting_reconciliation=ledger_posting_reconcile,
        ledger_exposure_reconciliation=ledger_exposure_reconcile,
    )

    handler = TransactionWorkerLambdaHandler(name="test_transaction_worker", dependency_loader=lambda: deps)
    event = {"Records": [_sns_record(domain)]}

    result = await handler.process_event(event)

    assert result["failed"] == 0
    expected_target = {
        "transaction": transaction.process_transaction,
        "transaction_debit": transaction_debit.process_job,
        "transaction_debit_reconcile": transaction_debit_reconcile.process_job,
        "transaction_debit_refund": transaction_debit_refund.process_job,
        "transaction_debit_refund_reconcile": transaction_debit_refund_reconcile.process_job,
        "direct_transfer_reconcile": direct_transfer_reconcile.process_job,
        "funding": funding.process_job,
        "funding_reconcile": funding_reconcile.process_job,
        "bill_fulfill": bill_fulfill.process_job,
        "bill_reconcile": bill_reconcile.process_job,
        "payout": payout.process_job,
        "payout_reconcile": payout_reconcile.process_job,
        "refund": refund.process_job,
        "refund_reconcile": refund_reconcile.process_job,
        "ledger_posting_reconcile": ledger_posting_reconcile.process_job,
        "ledger_exposure_reconcile": ledger_exposure_reconcile.process_job,
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
    deps: tuple[Any, ...] = (object(), object(), object(), object(), object(), object(), object())
    handler = TransactionWorkerLambdaHandler(name="test_transaction_worker", dependency_loader=lambda: deps)
    event = {"Records": [_sns_record("unknown")]}

    result = await handler.process_event(event)

    assert result["failed"] == 1
    assert result["batchItemFailures"] == [{"itemIdentifier": "m1"}]
