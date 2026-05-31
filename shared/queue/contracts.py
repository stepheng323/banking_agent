"""Authoritative queue/topic contract mappings for transport adapters."""

from dataclasses import dataclass
from typing import Literal

TopicType = Literal[
    "message.received",
    "transaction.execute",
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

DomainType = Literal[
    "transaction",
    "transaction_debit",
    "transaction_debit_reconcile",
    "transaction_debit_refund",
    "transaction_debit_refund_reconcile",
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
    "receipt",
    "notification",
]


@dataclass(frozen=True, slots=True)
class QueueContract:
    """Logical queue/topic contract used across Redis/SNS/SQS transports."""

    logical_topic: TopicType
    queue_name: str
    domain: DomainType | None = None
    sqs_queue_name: str | None = None
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
        domain="transaction",
        sqs_queue_name="banking-transactions",
        redis_stream_name="async:transactions",
    ),
    QueueContract(
        logical_topic="transaction_debit.process",
        queue_name="banking:transaction_debits",
        domain="transaction_debit",
        sqs_queue_name="banking-transactions",
        redis_stream_name="async:transaction_debits",
    ),
    QueueContract(
        logical_topic="transaction_debit.reconcile",
        queue_name="banking:transaction_debit_reconciliation",
        domain="transaction_debit_reconcile",
        sqs_queue_name="banking-transactions",
        redis_stream_name="async:transaction_debit_reconciliation",
    ),
    QueueContract(
        logical_topic="transaction_debit.refund",
        queue_name="banking:transaction_debit_refunds",
        domain="transaction_debit_refund",
        sqs_queue_name="banking-transactions",
        redis_stream_name="async:transaction_debit_refunds",
    ),
    QueueContract(
        logical_topic="transaction_debit.refund_reconcile",
        queue_name="banking:transaction_debit_refund_reconciliation",
        domain="transaction_debit_refund_reconcile",
        sqs_queue_name="banking-transactions",
        redis_stream_name="async:transaction_debit_refund_reconciliation",
    ),
    QueueContract(
        logical_topic="funding.process",
        queue_name="banking:funding",
        domain="funding",
        sqs_queue_name="banking-transactions",
        redis_stream_name="async:funding",
    ),
    QueueContract(
        logical_topic="funding.reconcile",
        queue_name="banking:funding_reconciliation",
        domain="funding_reconcile",
        sqs_queue_name="banking-transactions",
        redis_stream_name="async:funding_reconciliation",
    ),
    QueueContract(
        logical_topic="bill.fulfill",
        queue_name="banking:bill_fulfillment",
        domain="bill_fulfill",
        sqs_queue_name="banking-transactions",
        redis_stream_name="async:bill_fulfillment",
    ),
    QueueContract(
        logical_topic="bill.reconcile",
        queue_name="banking:bill_reconciliation",
        domain="bill_reconcile",
        sqs_queue_name="banking-transactions",
        redis_stream_name="async:bill_reconciliation",
    ),
    QueueContract(
        logical_topic="payout.process",
        queue_name="banking:payouts",
        domain="payout",
        sqs_queue_name="banking-transactions",
        redis_stream_name="async:payouts",
    ),
    QueueContract(
        logical_topic="payout.reconcile",
        queue_name="banking:payout_reconciliation",
        domain="payout_reconcile",
        sqs_queue_name="banking-transactions",
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
        domain="refund",
        sqs_queue_name="banking-transactions",
        redis_stream_name="async:refunds",
    ),
    QueueContract(
        logical_topic="refund.reconcile",
        queue_name="banking:refund_reconciliation",
        domain="refund_reconcile",
        sqs_queue_name="banking-transactions",
        redis_stream_name="async:refund_reconciliation",
    ),
    QueueContract(
        logical_topic="ledger.reconcile.postings",
        queue_name="banking:ledger_posting_reconciliation",
        domain="ledger_posting_reconcile",
        sqs_queue_name="banking-transactions",
        redis_stream_name="async:ledger_posting_reconciliation",
    ),
    QueueContract(
        logical_topic="ledger.reconcile.exposure",
        queue_name="banking:ledger_exposure_reconciliation",
        domain="ledger_exposure_reconcile",
        sqs_queue_name="banking-transactions",
        redis_stream_name="async:ledger_exposure_reconciliation",
    ),
    QueueContract(
        logical_topic="receipt.process",
        queue_name="banking:receipt_jobs",
        domain="receipt",
        sqs_queue_name="banking-receipts",
        redis_stream_name="async:receipts",
    ),
    QueueContract(
        logical_topic="notification.send",
        queue_name="banking:notifications",
        domain="notification",
        sqs_queue_name="banking-receipts",
        redis_stream_name="async:notifications",
    ),
)

CONTRACT_BY_TOPIC: dict[TopicType, QueueContract] = {contract.logical_topic: contract for contract in QUEUE_CONTRACTS}
CONTRACT_BY_QUEUE_NAME: dict[str, QueueContract] = {contract.queue_name: contract for contract in QUEUE_CONTRACTS}
CONTRACT_BY_DOMAIN: dict[str, QueueContract] = {
    contract.domain: contract for contract in QUEUE_CONTRACTS if contract.domain is not None
}
CONTRACT_BY_REDIS_STREAM: dict[str, QueueContract] = {
    contract.redis_stream_name: contract for contract in QUEUE_CONTRACTS if contract.redis_stream_name is not None
}


def get_contract_by_topic(topic: TopicType) -> QueueContract:
    """Return queue contract for logical topic."""
    return CONTRACT_BY_TOPIC[topic]


def get_contract_by_queue_name(queue_name: str) -> QueueContract | None:
    """Resolve contract by internal queue name."""
    return CONTRACT_BY_QUEUE_NAME.get(queue_name)


def resolve_contract_from_domain(domain: str) -> QueueContract | None:
    """Resolve contract from SNS message domain attribute."""
    return CONTRACT_BY_DOMAIN.get(domain)


def resolve_contract_from_redis_stream_name(stream_name: str) -> QueueContract | None:
    """Resolve contract from Redis stream name."""
    return CONTRACT_BY_REDIS_STREAM.get(stream_name)
