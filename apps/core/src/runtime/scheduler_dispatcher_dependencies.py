"""Dependency loader for schedule dispatcher lambda."""

from apps.core.src.runtime.common import require_aws_account_id
from apps.core.src.schedulers.transfer_schedule_dispatcher import TransferScheduleDispatcher
from shared.config.settings import settings
from shared.queue.factory import QueuePublisherFactory


def setup_schedule_dispatcher() -> TransferScheduleDispatcher:
    """Build dispatcher dependencies for periodic schedule tick."""
    require_aws_account_id()
    publisher = QueuePublisherFactory.get_async_publisher()
    return TransferScheduleDispatcher(
        publisher=publisher,
        max_due_per_tick=settings.schedule_max_due_per_tick,
    )
