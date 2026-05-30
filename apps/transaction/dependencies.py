"""Dependency loader for transaction worker runtimes."""

from banking.accounts.repositories.account_repository import AccountRepository
from banking.beneficiaries.services.suggestion_service import BeneficiarySuggestionService
from banking.transactions.repositories.transaction_repository import TransactionRepository
from banking.transactions.runtime.consumers.funding_consumer import FundingConsumer
from banking.transactions.runtime.consumers.funding_reconciliation_consumer import FundingReconciliationConsumer
from banking.transactions.runtime.consumers.payout_consumer import PayoutConsumer
from banking.transactions.runtime.consumers.payout_reconciliation_consumer import PayoutReconciliationConsumer
from banking.transactions.runtime.consumers.refund_consumer import RefundConsumer
from banking.transactions.runtime.consumers.refund_reconciliation_consumer import RefundReconciliationConsumer
from banking.transactions.runtime.consumers.transaction_consumer import TransactionConsumer
from banking.transactions.runtime.executors.airtime import AirtimeExecutor
from banking.transactions.runtime.executors.data import DataExecutor
from banking.transactions.runtime.executors.payout import PayoutExecutor
from banking.transactions.runtime.executors.transfer import TransferExecutor
from shared.cache.redis_client import RedisClient
from shared.clients.factories.providers import ProviderFactory
from shared.clients.providers.mono.direct_debit import MonoDirectDebitProvider
from shared.database.connection import get_db_session
from shared.queue.factory import QueuePublisherFactory


def setup_transaction_worker_consumers() -> tuple[
    TransactionConsumer,
    FundingConsumer,
    PayoutConsumer,
    PayoutReconciliationConsumer,
    RefundConsumer,
    FundingReconciliationConsumer,
    RefundReconciliationConsumer,
]:
    """Setup async transaction-domain consumers owned by transaction worker."""
    queue_publisher = QueuePublisherFactory.get_async_publisher()
    db_session = get_db_session()
    transaction_repository = TransactionRepository(db=db_session)
    account_repository = AccountRepository(db=db_session)
    direct_debit_provider = MonoDirectDebitProvider()
    payout_provider = ProviderFactory.get_payout_provider("flutterwave")
    if payout_provider is None:
        raise RuntimeError("Flutterwave payout provider is not configured")
    payout_resolver = ProviderFactory.get_resolver_for_flow("payout")
    if payout_resolver is None:
        raise RuntimeError("Payout resolver provider is not configured")

    bill_provider = ProviderFactory.get_bill_provider()
    if bill_provider is None:
        raise RuntimeError("Bill provider is not configured")
    redis_client = RedisClient.get_client()
    beneficiary_suggestion_service = BeneficiarySuggestionService(queue_publisher)
    airtime_executor = AirtimeExecutor(
        bill_provider=bill_provider,
        transaction_repo=transaction_repository,
        publisher=queue_publisher,
        redis_client=redis_client,
        beneficiary_suggestion_service=beneficiary_suggestion_service,
    )
    transfer_executor = TransferExecutor(
        direct_debit_provider=direct_debit_provider,
        account_repo=account_repository,
        transaction_repo=transaction_repository,
        publisher=queue_publisher,
        redis_client=redis_client,
        beneficiary_suggestion_service=beneficiary_suggestion_service,
    )
    data_executor = DataExecutor(
        bill_provider=bill_provider,
        transaction_repo=transaction_repository,
        redis_client=redis_client,
        beneficiary_suggestion_service=beneficiary_suggestion_service,
    )

    transaction_consumer = TransactionConsumer(
        transfer_executor=transfer_executor,
        airtime_executor=airtime_executor,
        data_executor=data_executor,
    )

    funding_consumer = FundingConsumer(
        publisher=queue_publisher,
        direct_debit_provider=direct_debit_provider,
    )
    funding_reconciliation_consumer = FundingReconciliationConsumer(
        direct_debit_provider=direct_debit_provider,
        publisher=queue_publisher,
    )
    payout_consumer = PayoutConsumer(
        payout_executor=PayoutExecutor(payout_provider=payout_provider, resolver_provider=payout_resolver),
        publisher=queue_publisher,
    )
    payout_reconciliation_consumer = PayoutReconciliationConsumer(
        payout_provider=payout_provider,
        publisher=queue_publisher,
    )
    refund_consumer = RefundConsumer(direct_debit_provider=direct_debit_provider)
    refund_reconciliation_consumer = RefundReconciliationConsumer(
        direct_debit_provider=direct_debit_provider,
        publisher=queue_publisher,
    )

    return (
        transaction_consumer,
        funding_consumer,
        payout_consumer,
        payout_reconciliation_consumer,
        refund_consumer,
        funding_reconciliation_consumer,
        refund_reconciliation_consumer,
    )
