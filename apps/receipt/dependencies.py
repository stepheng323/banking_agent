"""Dependency loader for receipt worker runtimes."""

from apps.receipt.src.consumer import ReceiptJobConsumer
from apps.receipt.src.notification_consumer import NotificationJobConsumer


def setup_receipt_worker_consumers() -> tuple[ReceiptJobConsumer, NotificationJobConsumer]:
    """Setup async receipt-domain consumer owned by the receipt runtime."""
    return ReceiptJobConsumer(), NotificationJobConsumer()
