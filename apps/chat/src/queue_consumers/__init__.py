"""Queue consumer package exports.

Avoid eager imports so runtime-specific workers can import only the consumers
they need without dragging in unrelated dependency trees.
"""

from typing import TYPE_CHECKING, Any

__all__ = [
    "MessageConsumer",
]

if TYPE_CHECKING:
    from apps.chat.src.queue_consumers.message_consumer import MessageConsumer


def __getattr__(name: str) -> Any:
    if name == "MessageConsumer":
        from apps.chat.src.queue_consumers.message_consumer import MessageConsumer

        return MessageConsumer
    raise AttributeError(f"module 'apps.chat.src.queue_consumers' has no attribute '{name}'")
