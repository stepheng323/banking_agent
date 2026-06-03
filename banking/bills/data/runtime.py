"""Runtime factory for data bill-payment workers."""

from typing import Any

from langchain_core.language_models import BaseChatModel

from banking.bills.data.extraction.extractor import DataEntityExtractor
from banking.bills.data.worker import DataWorker


def build_data_worker(
    *,
    extractor_llm: BaseChatModel,
    bill_provider: Any,
    transaction_repo: Any,
    publisher: Any,
    redis_client: Any | None = None,
) -> DataWorker:
    """Build the data worker through the bill-payment domain boundary."""
    return DataWorker(
        extractor=DataEntityExtractor(llm=extractor_llm),
        bill_provider=bill_provider,
        transaction_repo=transaction_repo,
        publisher=publisher,
        redis_client=redis_client,
    )
