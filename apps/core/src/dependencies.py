"""Compatibility wrappers for runtime dependency loaders.

Prefer importing directly from:
- apps.core.src.runtime.core_chat_dependencies
- apps.core.src.runtime.transaction_worker_dependencies
- apps.core.src.runtime.receipt_worker_dependencies
"""

from typing import Any

from apps.receipt.src.consumer import ReceiptJobConsumer


def setup_core_consumers() -> tuple[Any, Any]:
    from apps.core.src.runtime.core_chat_dependencies import setup_core_consumers as _setup_core_consumers

    return _setup_core_consumers()


def setup_transaction_worker_consumers() -> tuple[Any, Any, Any, Any]:
    from apps.core.src.runtime.transaction_worker_dependencies import (
        setup_transaction_worker_consumers as _setup_transaction_worker_consumers,
    )

    return _setup_transaction_worker_consumers()


def setup_receipt_worker_consumers() -> ReceiptJobConsumer:
    from apps.core.src.runtime.receipt_worker_dependencies import (
        setup_receipt_worker_consumers as _setup_receipt_worker_consumers,
    )

    return _setup_receipt_worker_consumers()
