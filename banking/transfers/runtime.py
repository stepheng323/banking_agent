"""Runtime factory for transfer-domain workers."""

from typing import Any

from langchain_core.language_models import BaseChatModel

from banking.transfers.extraction.extractor import TransferEntityExtractor
from banking.transfers.worker import TransferWorker


def build_transfer_worker(
    *,
    extractor_llm: BaseChatModel,
    publisher: Any,
    resolver_provider: Any,
    bank_cache: Any,
    transaction_repo: Any,
    dd_provider: Any | None = None,
    redis_client: Any | None = None,
    payout_resolver_provider: Any | None = None,
    payout_bank_cache: Any | None = None,
    validation_service: Any | None = None,
    risk_advisory_enabled: bool = True,
) -> TransferWorker:
    """Build the transfer worker through the transfer domain boundary."""
    return TransferWorker(
        validation_service=validation_service,
        publisher=publisher,
        extractor=TransferEntityExtractor(llm=extractor_llm),
        resolver_provider=resolver_provider,
        bank_cache=bank_cache,
        transaction_repo=transaction_repo,
        payout_resolver_provider=payout_resolver_provider,
        payout_bank_cache=payout_bank_cache,
        dd_provider=dd_provider,
        redis_client=redis_client,
        risk_advisory_enabled=risk_advisory_enabled,
    )
