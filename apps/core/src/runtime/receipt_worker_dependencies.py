"""Dependency loader for receipt-domain Lambda worker."""

from apps.receipt.src.consumer import ReceiptJobConsumer


def setup_receipt_worker_consumers() -> ReceiptJobConsumer:
    """Setup async receipt-domain consumer owned by receipt Lambda."""
    return ReceiptJobConsumer()
