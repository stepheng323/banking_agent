"""Compatibility wrappers for runtime dependency loaders.

Prefer importing directly from:
- apps.core.src.runtime.core_chat_dependencies
- apps.core.src.runtime.transaction_worker_dependencies
- apps.core.src.runtime.messaging_worker_dependencies
"""

from apps.core.src.queue_consumers import (
    FundingConsumer,
    MessageConsumer,
    OutboxConsumer,
    PayoutConsumer,
    RefundConsumer,
    TransactionConsumer,
)
from apps.core.src.queue_consumers.actionable_consumer import ActionableMessageConsumer
from apps.core.src.queue_consumers.flow_event_consumer import FlowEventConsumer


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
