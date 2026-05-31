"""Consolidated Lambda handler for async transaction-domain queues."""

from typing import Any

from apps.transaction.dependencies import setup_transaction_worker_consumers
from shared.queue.lambda_base import BaseSQSHandler
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class TransactionWorkerLambdaHandler(BaseSQSHandler):
    """Routes transaction-domain jobs to the correct consumer based on queue contract."""

    async def process_record(self, payload: dict, deps: Any) -> None:
        context = self.get_active_record_context()
        domain = context.get("domain")
        queue_name = context.get("queue_name")
        message_id = context.get("message_id")

        if domain == "transaction":
            await deps.transaction.process_transaction(payload)
        elif domain == "transaction_debit":
            await deps.transaction_debit.process_job(payload)
        elif domain == "transaction_debit_reconcile":
            await deps.transaction_debit_reconciliation.process_job(payload)
        elif domain == "transaction_debit_refund":
            await deps.transaction_debit_refund.process_job(payload)
        elif domain == "transaction_debit_refund_reconcile":
            await deps.transaction_debit_refund_reconciliation.process_job(payload)
        elif domain == "funding":
            await deps.funding.process_job(payload)
        elif domain == "funding_reconcile":
            await deps.funding_reconciliation.process_job(payload)
        elif domain == "bill_fulfill":
            await deps.bill_fulfillment.process_job(payload)
        elif domain == "bill_reconcile":
            await deps.bill_reconciliation.process_job(payload)
        elif domain == "payout":
            await deps.payout.process_job(payload)
        elif domain == "payout_reconcile":
            await deps.payout_reconciliation.process_job(payload)
        elif domain == "refund":
            await deps.refund.process_job(payload)
        elif domain == "refund_reconcile":
            await deps.refund_reconciliation.process_job(payload)
        elif domain == "ledger_posting_reconcile":
            await deps.ledger_posting_reconciliation.process_job(payload)
        elif domain == "ledger_exposure_reconcile":
            await deps.ledger_exposure_reconciliation.process_job(payload)
        else:
            raise ValueError(f"transaction_worker_unknown_route domain={domain} queue_name={queue_name}")

        logger.info(
            "worker_record_processed",
            worker_name="transaction-worker",
            queue_name=queue_name,
            domain=domain,
            message_id=message_id,
        )


_handler = TransactionWorkerLambdaHandler(
    name="transaction_worker",
    dependency_loader=setup_transaction_worker_consumers,
)


def handler(event: dict, context: Any) -> dict:
    return _handler.handle(event, context)
