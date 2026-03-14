"""Authoritative queue/topic contract mappings for transport adapters."""

from dataclasses import dataclass
from typing import Literal

TopicType = Literal[
    "message.received",
    "transaction.execute",
    "funding.process",
    "payout.process",
    "flow_event.process",
    "refund.process",
    "receipt.process",
]

DomainType = Literal[
    "transaction",
    "funding",
    "payout",
    "refund",
    "receipt",
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
        logical_topic="funding.process",
        queue_name="banking:funding",
        domain="funding",
        sqs_queue_name="banking-transactions",
        redis_stream_name="async:funding",
    ),
    QueueContract(
        logical_topic="payout.process",
        queue_name="banking:payouts",
        domain="payout",
        sqs_queue_name="banking-transactions",
        redis_stream_name="async:payouts",
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
        logical_topic="receipt.process",
        queue_name="banking:receipt_jobs",
        domain="receipt",
        sqs_queue_name="banking-receipts",
        redis_stream_name="async:receipts",
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
