"""Dependency loader for transaction-domain Lambda worker."""

from apps.core.src.agent.executors.airtime import AirtimeExecutor
from apps.core.src.agent.executors.data import DataExecutor
from apps.core.src.agent.executors.payout import PayoutExecutor
from apps.core.src.agent.executors.transfer import TransferExecutor
from apps.core.src.queue_consumers.funding_consumer import FundingConsumer
from apps.core.src.queue_consumers.payout_consumer import PayoutConsumer
from apps.core.src.queue_consumers.refund_consumer import RefundConsumer
from apps.core.src.queue_consumers.transaction_consumer import TransactionConsumer
from apps.core.src.runtime.common import require_aws_account_id
from shared.clients.factories.providers import ProviderFactory
from shared.clients.providers.mono.direct_debit import MonoDirectDebitProvider
from shared.database.connection import get_db_session
from shared.queue.factory import QueuePublisherFactory
from shared.repositories.account_repository import AccountRepository
from shared.repositories.transaction_repository import TransactionRepository


def setup_transaction_worker_consumers() -> tuple[
    TransactionConsumer,
    FundingConsumer,
    PayoutConsumer,
    RefundConsumer,
]:
    """Setup async transaction-domain consumers owned by transaction Lambda worker."""
    require_aws_account_id()
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
    airtime_executor = AirtimeExecutor(
        bill_provider=bill_provider,
        transaction_repo=transaction_repository,
        publisher=queue_publisher,
    )
    transfer_executor = TransferExecutor(
        direct_debit_provider=direct_debit_provider,
        account_repo=account_repository,
        transaction_repo=transaction_repository,
    )
    data_executor = DataExecutor(
        bill_provider=bill_provider,
        transaction_repo=transaction_repository,
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
    payout_consumer = PayoutConsumer(
        payout_executor=PayoutExecutor(payout_provider=payout_provider, resolver_provider=payout_resolver),
    )
    refund_consumer = RefundConsumer(direct_debit_provider=direct_debit_provider)

    return (
        transaction_consumer,
        funding_consumer,
        payout_consumer,
        refund_consumer,
    )
