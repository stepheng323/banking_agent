"""Dependency loader for schedule dispatcher runtime."""

from banking.scheduling.services.transaction_schedule_dispatcher import TransactionScheduleDispatcher
from shared.config.settings import settings
from shared.queue.factory import QueuePublisherFactory


def setup_schedule_dispatcher() -> TransactionScheduleDispatcher:
    """Build dispatcher dependencies for periodic schedule tick."""
    publisher = QueuePublisherFactory.get_async_publisher()
    return TransactionScheduleDispatcher(
        publisher=publisher,
        max_due_per_tick=settings.schedule_max_due_per_tick,
    )
