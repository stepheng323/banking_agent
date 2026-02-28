"""Authoritative queue/topic contract mappings for transport adapters."""

from dataclasses import dataclass
from typing import Literal

TopicType = Literal[
    "message.received",
    "transaction.execute",
    "funding.process",
    "payout.process",
    "notification.send",
    "flow_event.process",
    "refund.process",
    "receipt.process",
    "actionable_message.send",
    "schedule.trigger",
]


@dataclass(frozen=True, slots=True)
class QueueContract:
    """Logical queue/topic contract used across Redis/SNS/SQS transports."""

    logical_topic: TopicType
    queue_name: str
    sns_topic_name: str
    sqs_queue_name: str


QUEUE_CONTRACTS: tuple[QueueContract, ...] = (
    QueueContract(
        logical_topic="message.received",
        queue_name="banking:messages",
        sns_topic_name="message-received",
        sqs_queue_name="banking-messages",
    ),
    QueueContract(
        logical_topic="transaction.execute",
        queue_name="banking:transactions",
        sns_topic_name="transaction-execute",
        sqs_queue_name="banking-transactions",
    ),
    QueueContract(
        logical_topic="funding.process",
        queue_name="banking:funding",
        sns_topic_name="funding-process",
        sqs_queue_name="banking-funding",
    ),
    QueueContract(
        logical_topic="payout.process",
        queue_name="banking:payouts",
        sns_topic_name="payout-process",
        sqs_queue_name="banking-payouts",
    ),
    QueueContract(
        logical_topic="notification.send",
        queue_name="banking:outbox",
        sns_topic_name="notification-send",
        sqs_queue_name="banking-outbox",
    ),
    QueueContract(
        logical_topic="flow_event.process",
        queue_name="banking:flow_events",
        sns_topic_name="flow-event-process",
        sqs_queue_name="banking-flow-events",
    ),
    QueueContract(
        logical_topic="refund.process",
        queue_name="banking:refunds",
        sns_topic_name="refund-process",
        sqs_queue_name="banking-refunds",
    ),
    QueueContract(
        logical_topic="receipt.process",
        queue_name="banking:receipt_jobs",
        sns_topic_name="receipt-process",
        sqs_queue_name="banking-receipt-jobs",
    ),
    QueueContract(
        logical_topic="actionable_message.send",
        queue_name="banking:actionable_messages",
        sns_topic_name="actionable-message-send",
        sqs_queue_name="banking-actionable-messages",
    ),
    # Schedule is reserved for the next phase; mapping kept explicit now.
    QueueContract(
        logical_topic="schedule.trigger",
        queue_name="banking:schedule_trigger",
        sns_topic_name="schedule-trigger",
        sqs_queue_name="banking-schedule-trigger",
    ),
)

CONTRACT_BY_TOPIC: dict[TopicType, QueueContract] = {contract.logical_topic: contract for contract in QUEUE_CONTRACTS}
CONTRACT_BY_QUEUE_NAME: dict[str, QueueContract] = {contract.queue_name: contract for contract in QUEUE_CONTRACTS}
CONTRACT_BY_SNS_TOPIC: dict[str, QueueContract] = {contract.sns_topic_name: contract for contract in QUEUE_CONTRACTS}
CONTRACT_BY_SQS_QUEUE: dict[str, QueueContract] = {contract.sqs_queue_name: contract for contract in QUEUE_CONTRACTS}


def get_contract_by_topic(topic: TopicType) -> QueueContract:
    """Return queue contract for logical topic."""
    return CONTRACT_BY_TOPIC[topic]


def get_contract_by_queue_name(queue_name: str) -> QueueContract | None:
    """Resolve contract by internal queue name."""
    return CONTRACT_BY_QUEUE_NAME.get(queue_name)


def resolve_contract_from_sns_topic_name(topic_name: str) -> QueueContract | None:
    """Resolve contract from raw SNS topic resource name."""
    if topic_name in CONTRACT_BY_SNS_TOPIC:
        return CONTRACT_BY_SNS_TOPIC[topic_name]

    for topic_key, contract in CONTRACT_BY_SNS_TOPIC.items():
        if topic_name.endswith(f"-{topic_key}") or f"-{topic_key}-" in topic_name:
            return contract
    return None


def resolve_contract_from_sns_topic_arn(topic_arn: str) -> QueueContract | None:
    """Resolve contract from SNS topic ARN."""
    topic_name = topic_arn.rsplit(":", maxsplit=1)[-1]
    return resolve_contract_from_sns_topic_name(topic_name)


def resolve_contract_from_sqs_queue_name(queue_name: str) -> QueueContract | None:
    """Resolve contract from SQS queue resource name."""
    if queue_name in CONTRACT_BY_SQS_QUEUE:
        return CONTRACT_BY_SQS_QUEUE[queue_name]

    for queue_key, contract in CONTRACT_BY_SQS_QUEUE.items():
        if queue_name.endswith(f"-{queue_key}") or f"-{queue_key}-" in queue_name:
            return contract
    return None
