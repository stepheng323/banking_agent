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
from shared.clients.factories.payment import PaymentProviderFactory
from shared.clients.providers.mono.banking import MonoBankingProvider
from shared.clients.providers.mono.direct_debit import MonoDirectDebitProvider
from shared.database.connection import get_db_session
from shared.queue.factory import QueuePublisherFactory
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
    transaction_repository = TransactionRepository(db=get_db_session())
    banking_provider = MonoBankingProvider()
    direct_debit_provider = MonoDirectDebitProvider()
    payout_provider = PaymentProviderFactory.get_provider_by_name("flutterwave")
    if payout_provider is None:
        payout_provider = PaymentProviderFactory.create_provider("flutterwave")
    if payout_provider is None:
        raise RuntimeError("Flutterwave payout provider is not configured")

    bill_provider = PaymentProviderFactory.get_bill_payment_provider()
    airtime_executor = AirtimeExecutor(
        bill_provider=bill_provider,
        transaction_repo=transaction_repository,
        publisher=queue_publisher,
    )
    transfer_executor = TransferExecutor(
        banking_provider=banking_provider,
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
        payout_executor=PayoutExecutor(payment_provider=payout_provider),
    )
    refund_consumer = RefundConsumer(direct_debit_provider=direct_debit_provider)

    return (
        transaction_consumer,
        funding_consumer,
        payout_consumer,
        refund_consumer,
    )
