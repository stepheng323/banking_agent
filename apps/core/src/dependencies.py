"""Compatibility wrappers for runtime dependency loaders.

Prefer importing directly from:
- apps.core.src.runtime.core_chat_dependencies
- apps.core.src.runtime.transaction_worker_dependencies
- apps.core.src.runtime.messaging_worker_dependencies
"""

from apps.core.src.queue_consumers.actionable_consumer import ActionableMessageConsumer
from apps.core.src.queue_consumers.flow_event_consumer import FlowEventConsumer
from apps.core.src.queue_consumers.funding_consumer import FundingConsumer
from apps.core.src.queue_consumers.message_consumer import MessageConsumer
from apps.core.src.queue_consumers.outbox_consumer import OutboxConsumer
from apps.core.src.queue_consumers.payout_consumer import PayoutConsumer
from apps.core.src.queue_consumers.refund_consumer import RefundConsumer
from apps.core.src.queue_consumers.transaction_consumer import TransactionConsumer


def setup_core_consumers() -> tuple[MessageConsumer, FlowEventConsumer]:
    from apps.core.src.runtime.core_chat_dependencies import setup_core_consumers as _setup_core_consumers

    return _setup_core_consumers()


def setup_transaction_worker_consumers() -> tuple[
    TransactionConsumer,
    FundingConsumer,
    PayoutConsumer,
    RefundConsumer,
]:
    from apps.core.src.runtime.transaction_worker_dependencies import (
        setup_transaction_worker_consumers as _setup_transaction_worker_consumers,
    )

    return _setup_transaction_worker_consumers()


def setup_messaging_worker_consumers() -> tuple[OutboxConsumer, ActionableMessageConsumer]:
    from apps.core.src.runtime.messaging_worker_dependencies import (
        setup_messaging_worker_consumers as _setup_messaging_worker_consumers,
    )

    return _setup_messaging_worker_consumers()
