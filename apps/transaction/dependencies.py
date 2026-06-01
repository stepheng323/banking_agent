"""Dependency loader for transaction worker runtimes."""

from dataclasses import dataclass
from typing import cast

from banking.accounts.repositories.account_repository import AccountRepository
from banking.beneficiaries.services.suggestion_service import BeneficiarySuggestionService
from banking.ledger.reconciliation import LedgerExposureReconciliationConsumer, LedgerPostingReconciliationConsumer
from banking.messaging.delivery.service import DeliveryService
from banking.transactions.repositories.transaction_repository import TransactionRepository
from banking.transactions.runtime.async_group_types import AsyncGroupRedis
from banking.transactions.runtime.bill_completion_notifications import BillCompletionNotifier
from banking.transactions.runtime.consumers.bill_fulfillment_consumer import (
    BillFulfillmentConsumer,
    BillReconciliationConsumer,
)
from banking.transactions.runtime.consumers.direct_transfer_reconciliation_consumer import (
    DirectTransferReconciliationConsumer,
)
from banking.transactions.runtime.consumers.funding_consumer import FundingConsumer
from banking.transactions.runtime.consumers.funding_reconciliation_consumer import FundingReconciliationConsumer
from banking.transactions.runtime.consumers.payout_consumer import PayoutConsumer
from banking.transactions.runtime.consumers.payout_reconciliation_consumer import PayoutReconciliationConsumer
from banking.transactions.runtime.consumers.refund_consumer import RefundConsumer
from banking.transactions.runtime.consumers.refund_reconciliation_consumer import RefundReconciliationConsumer
from banking.transactions.runtime.consumers.transaction_consumer import TransactionConsumer
from banking.transactions.runtime.consumers.transaction_debit_consumer import (
    TransactionDebitConsumer,
    TransactionDebitReconciliationConsumer,
)
from banking.transactions.runtime.consumers.transaction_debit_refund_consumer import (
    TransactionDebitRefundConsumer,
    TransactionDebitRefundReconciliationConsumer,
)
from banking.transactions.runtime.executors.airtime import AirtimeExecutor
from banking.transactions.runtime.executors.data import DataExecutor
from banking.transactions.runtime.executors.payout import PayoutExecutor
from banking.transactions.runtime.executors.transfer import TransferExecutor
from shared.cache.redis_client import RedisClient
from shared.clients.factories.providers import ProviderFactory
from shared.clients.providers.mono.direct_debit import MonoDirectDebitProvider
from shared.database.connection import get_db_session
from shared.queue.factory import QueuePublisherFactory


@dataclass(frozen=True, slots=True)
class TransactionWorkerConsumers:
    """Transaction worker consumer bundle keyed by domain."""

    transaction: TransactionConsumer
    direct_transfer_reconciliation: DirectTransferReconciliationConsumer
    transaction_debit: TransactionDebitConsumer
    transaction_debit_reconciliation: TransactionDebitReconciliationConsumer
    transaction_debit_refund: TransactionDebitRefundConsumer
    transaction_debit_refund_reconciliation: TransactionDebitRefundReconciliationConsumer
    funding: FundingConsumer
    bill_fulfillment: BillFulfillmentConsumer
    bill_reconciliation: BillReconciliationConsumer
    payout: PayoutConsumer
    payout_reconciliation: PayoutReconciliationConsumer
    refund: RefundConsumer
    funding_reconciliation: FundingReconciliationConsumer
    refund_reconciliation: RefundReconciliationConsumer
    ledger_posting_reconciliation: LedgerPostingReconciliationConsumer
    ledger_exposure_reconciliation: LedgerExposureReconciliationConsumer


def setup_transaction_worker_consumers() -> TransactionWorkerConsumers:
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
    async_group_redis = cast(AsyncGroupRedis, redis_client)
    beneficiary_suggestion_service = BeneficiarySuggestionService(queue_publisher)
    delivery_service = DeliveryService()
    bill_completion_notifier = BillCompletionNotifier(
        delivery_service=delivery_service,
        redis_client=async_group_redis,
        beneficiary_suggestion_service=beneficiary_suggestion_service,
    )
    airtime_executor = AirtimeExecutor(
        bill_provider=bill_provider,
        transaction_repo=transaction_repository,
        publisher=queue_publisher,
        delivery_service=delivery_service,
        redis_client=async_group_redis,
        beneficiary_suggestion_service=beneficiary_suggestion_service,
    )
    transfer_executor = TransferExecutor(
        direct_debit_provider=direct_debit_provider,
        account_repo=account_repository,
        transaction_repo=transaction_repository,
        publisher=queue_publisher,
        redis_client=async_group_redis,
        beneficiary_suggestion_service=beneficiary_suggestion_service,
    )
    data_executor = DataExecutor(
        bill_provider=bill_provider,
        transaction_repo=transaction_repository,
        delivery_service=delivery_service,
        redis_client=async_group_redis,
        beneficiary_suggestion_service=beneficiary_suggestion_service,
        publisher=queue_publisher,
    )

    transaction_consumer = TransactionConsumer(
        transfer_executor=transfer_executor,
        airtime_executor=airtime_executor,
        data_executor=data_executor,
    )
    direct_transfer_reconciliation_consumer = DirectTransferReconciliationConsumer(
        direct_debit_provider=direct_debit_provider,
    )

    funding_consumer = FundingConsumer(
        publisher=queue_publisher,
        direct_debit_provider=direct_debit_provider,
    )
    transaction_debit_consumer = TransactionDebitConsumer(
        direct_debit_provider=direct_debit_provider,
        publisher=queue_publisher,
        notifier=bill_completion_notifier,
    )
    transaction_debit_reconciliation_consumer = TransactionDebitReconciliationConsumer(
        direct_debit_provider=direct_debit_provider,
        publisher=queue_publisher,
        notifier=bill_completion_notifier,
    )
    transaction_debit_refund_consumer = TransactionDebitRefundConsumer(
        direct_debit_provider=direct_debit_provider,
        notifier=bill_completion_notifier,
    )
    transaction_debit_refund_reconciliation_consumer = TransactionDebitRefundReconciliationConsumer(
        direct_debit_provider=direct_debit_provider,
        notifier=bill_completion_notifier,
    )
    bill_fulfillment_consumer = BillFulfillmentConsumer(
        bill_provider=bill_provider,
        publisher=queue_publisher,
        notifier=bill_completion_notifier,
    )
    bill_reconciliation_consumer = BillReconciliationConsumer(
        bill_provider=bill_provider,
        publisher=queue_publisher,
        notifier=bill_completion_notifier,
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

    return TransactionWorkerConsumers(
        transaction=transaction_consumer,
        direct_transfer_reconciliation=direct_transfer_reconciliation_consumer,
        transaction_debit=transaction_debit_consumer,
        transaction_debit_reconciliation=transaction_debit_reconciliation_consumer,
        transaction_debit_refund=transaction_debit_refund_consumer,
        transaction_debit_refund_reconciliation=transaction_debit_refund_reconciliation_consumer,
        funding=funding_consumer,
        bill_fulfillment=bill_fulfillment_consumer,
        bill_reconciliation=bill_reconciliation_consumer,
        payout=payout_consumer,
        payout_reconciliation=payout_reconciliation_consumer,
        refund=refund_consumer,
        funding_reconciliation=funding_reconciliation_consumer,
        refund_reconciliation=refund_reconciliation_consumer,
        ledger_posting_reconciliation=LedgerPostingReconciliationConsumer(),
        ledger_exposure_reconciliation=LedgerExposureReconciliationConsumer(),
    )
