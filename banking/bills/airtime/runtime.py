"""Runtime factory for airtime bill-payment workers."""

from typing import Any

from langchain_core.language_models import BaseChatModel

from banking.bills.airtime.extractor import AirtimeEntityExtractor
from banking.bills.airtime.worker import AirtimeWorker


def build_airtime_worker(
    *,
    extractor_llm: BaseChatModel,
    bill_provider: Any,
    transaction_repo: Any,
    publisher: Any,
    redis_client: Any | None = None,
) -> AirtimeWorker:
    """Build the airtime worker through the bill-payment domain boundary."""
    return AirtimeWorker(
        extractor=AirtimeEntityExtractor(llm=extractor_llm),
        bill_provider=bill_provider,
        transaction_repo=transaction_repo,
        publisher=publisher,
        redis_client=redis_client,
    )
