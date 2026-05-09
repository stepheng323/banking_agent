"""Dependency loader for receipt worker runtimes."""

from apps.receipt.src.consumer import ReceiptJobConsumer


def setup_receipt_worker_consumers() -> ReceiptJobConsumer:
    """Setup async receipt-domain consumer owned by the receipt runtime."""
    return ReceiptJobConsumer()

