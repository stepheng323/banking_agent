"""Authoritative queue/topic contract mappings for Redis stream adapters."""

from dataclasses import dataclass
from typing import Literal

TopicType = Literal[
    "message.received",
    "transaction.execute",
    "direct_transfer.reconcile",
    "transaction_debit.process",
    "transaction_debit.reconcile",
    "transaction_debit.refund",
    "transaction_debit.refund_reconcile",
    "funding.process",
    "funding.reconcile",
    "bill.fulfill",
    "bill.reconcile",
    "payout.process",
    "payout.reconcile",
    "flow_event.process",
    "refund.process",
    "refund.reconcile",
    "ledger.reconcile.postings",
    "ledger.reconcile.exposure",
    "receipt.process",
    "notification.send",
]


@dataclass(frozen=True, slots=True)
class QueueContract:
    """Logical queue/topic contract used by Redis stream transports."""

    logical_topic: TopicType
    queue_name: str
    redis_stream_name: str | None = None


QUEUE_CONTRACTS: tuple[QueueContract, ...] = (
    QueueContract(
        logical_topic="message.received",
        queue_name="banking:messages",
        redis_stream_name="chat:messages",
    ),
    QueueContract(
        logical_topic="transaction.execute",
        queue_name="banking:transactions",
        redis_stream_name="async:transactions",
    ),
    QueueContract(
        logical_topic="direct_transfer.reconcile",
        queue_name="banking:direct_transfer_reconciliation",
        redis_stream_name="async:direct_transfer_reconciliation",
    ),
    QueueContract(
        logical_topic="transaction_debit.process",
        queue_name="banking:transaction_debits",
        redis_stream_name="async:transaction_debits",
    ),
    QueueContract(
        logical_topic="transaction_debit.reconcile",
        queue_name="banking:transaction_debit_reconciliation",
        redis_stream_name="async:transaction_debit_reconciliation",
    ),
    QueueContract(
        logical_topic="transaction_debit.refund",
        queue_name="banking:transaction_debit_refunds",
        redis_stream_name="async:transaction_debit_refunds",
    ),
    QueueContract(
        logical_topic="transaction_debit.refund_reconcile",
        queue_name="banking:transaction_debit_refund_reconciliation",
        redis_stream_name="async:transaction_debit_refund_reconciliation",
    ),
    QueueContract(
        logical_topic="funding.process",
        queue_name="banking:funding",
        redis_stream_name="async:funding",
    ),
    QueueContract(
        logical_topic="funding.reconcile",
        queue_name="banking:funding_reconciliation",
        redis_stream_name="async:funding_reconciliation",
    ),
    QueueContract(
        logical_topic="bill.fulfill",
        queue_name="banking:bill_fulfillment",
        redis_stream_name="async:bill_fulfillment",
    ),
    QueueContract(
        logical_topic="bill.reconcile",
        queue_name="banking:bill_reconciliation",
        redis_stream_name="async:bill_reconciliation",
    ),
    QueueContract(
        logical_topic="payout.process",
        queue_name="banking:payouts",
        redis_stream_name="async:payouts",
    ),
    QueueContract(
        logical_topic="payout.reconcile",
        queue_name="banking:payout_reconciliation",
        redis_stream_name="async:payout_reconciliation",
    ),
    QueueContract(
        logical_topic="flow_event.process",
        queue_name="banking:flow_events",
        redis_stream_name="chat:flow_events",
    ),
    QueueContract(
        logical_topic="refund.process",
        queue_name="banking:refunds",
        redis_stream_name="async:refunds",
    ),
    QueueContract(
        logical_topic="refund.reconcile",
        queue_name="banking:refund_reconciliation",
        redis_stream_name="async:refund_reconciliation",
    ),
    QueueContract(
        logical_topic="ledger.reconcile.postings",
        queue_name="banking:ledger_posting_reconciliation",
        redis_stream_name="async:ledger_posting_reconciliation",
    ),
    QueueContract(
        logical_topic="ledger.reconcile.exposure",
        queue_name="banking:ledger_exposure_reconciliation",
        redis_stream_name="async:ledger_exposure_reconciliation",
    ),
    QueueContract(
        logical_topic="receipt.process",
        queue_name="banking:receipt_jobs",
        redis_stream_name="async:receipts",
    ),
    QueueContract(
        logical_topic="notification.send",
        queue_name="banking:notifications",
        redis_stream_name="async:notifications",
    ),
)

CONTRACT_BY_TOPIC: dict[TopicType, QueueContract] = {contract.logical_topic: contract for contract in QUEUE_CONTRACTS}
CONTRACT_BY_QUEUE_NAME: dict[str, QueueContract] = {contract.queue_name: contract for contract in QUEUE_CONTRACTS}
CONTRACT_BY_REDIS_STREAM: dict[str, QueueContract] = {
    contract.redis_stream_name: contract for contract in QUEUE_CONTRACTS if contract.redis_stream_name is not None
}


def get_contract_by_topic(topic: TopicType) -> QueueContract:
    """Return queue contract for logical topic."""
    return CONTRACT_BY_TOPIC[topic]


def get_contract_by_queue_name(queue_name: str) -> QueueContract | None:
    """Resolve contract by internal queue name."""
    return CONTRACT_BY_QUEUE_NAME.get(queue_name)


def resolve_contract_from_redis_stream_name(stream_name: str) -> QueueContract | None:
    """Resolve contract from Redis stream name."""
    return CONTRACT_BY_REDIS_STREAM.get(stream_name)
