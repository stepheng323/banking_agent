"""Dependency loader for schedule dispatcher lambda."""

from apps.chat.src.runtime.common import require_aws_account_id
from apps.chat.src.schedulers.transfer_schedule_dispatcher import TransactionScheduleDispatcher
from shared.config.settings import settings
from shared.queue.factory import QueuePublisherFactory


def setup_schedule_dispatcher() -> TransactionScheduleDispatcher:
    """Build dispatcher dependencies for periodic schedule tick."""
    require_aws_account_id()
    publisher = QueuePublisherFactory.get_async_publisher()
    return TransactionScheduleDispatcher(
        publisher=publisher,
        max_due_per_tick=settings.schedule_max_due_per_tick,
    )
