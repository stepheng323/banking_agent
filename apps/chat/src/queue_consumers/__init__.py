"""Queue consumer package exports.

Avoid eager imports so runtime-specific workers can import only the consumers
they need without dragging in unrelated dependency trees.
"""

from typing import TYPE_CHECKING, Any

__all__ = [
    "MessageConsumer",
    "TransactionConsumer",
    "FundingConsumer",
    "PayoutConsumer",
    "RefundConsumer",
]

if TYPE_CHECKING:
    from apps.chat.src.queue_consumers.funding_consumer import FundingConsumer
    from apps.chat.src.queue_consumers.message_consumer import MessageConsumer
    from apps.chat.src.queue_consumers.payout_consumer import PayoutConsumer
    from apps.chat.src.queue_consumers.refund_consumer import RefundConsumer
    from apps.chat.src.queue_consumers.transaction_consumer import TransactionConsumer


def __getattr__(name: str) -> Any:
    if name == "MessageConsumer":
        from apps.chat.src.queue_consumers.message_consumer import MessageConsumer

        return MessageConsumer
    if name == "TransactionConsumer":
        from apps.chat.src.queue_consumers.transaction_consumer import TransactionConsumer

        return TransactionConsumer
    if name == "FundingConsumer":
        from apps.chat.src.queue_consumers.funding_consumer import FundingConsumer

        return FundingConsumer
    if name == "PayoutConsumer":
        from apps.chat.src.queue_consumers.payout_consumer import PayoutConsumer

        return PayoutConsumer
    if name == "RefundConsumer":
        from apps.chat.src.queue_consumers.refund_consumer import RefundConsumer

        return RefundConsumer
    raise AttributeError(f"module 'apps.chat.src.queue_consumers' has no attribute '{name}'")
