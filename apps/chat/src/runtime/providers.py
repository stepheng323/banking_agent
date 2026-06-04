"""Provider capability resolution for the chat runtime."""

from dataclasses import dataclass

from shared.clients.abstractions.banking import BankDataProvider
from shared.clients.abstractions.bill import BillPaymentProvider
from shared.clients.abstractions.direct_debit import DirectDebitProvider
from shared.clients.abstractions.resolution import AccountResolverProvider
from shared.clients.factories.providers import ProviderFactory
from shared.config.settings import settings


@dataclass(slots=True)
class ChatRuntimeProviders:
    """External providers required by the chat runtime workers."""

    bank_data_provider: BankDataProvider
    resolver_provider: AccountResolverProvider
    payout_resolver_provider: AccountResolverProvider
    direct_debit_provider: DirectDebitProvider
    bill_provider: BillPaymentProvider


def build_chat_runtime_providers() -> ChatRuntimeProviders:
    """Resolve configured runtime providers and fail fast on missing capabilities."""
    bank_data_provider = ProviderFactory.get_bank_data_provider()
    if bank_data_provider is None:
        raise RuntimeError(f"Account provider bank-data capability is not configured: {settings.account_provider_name}")

    resolver_provider = ProviderFactory.get_resolver_for_flow("transfer")
    if resolver_provider is None:
        raise RuntimeError(f"Transfer resolver provider is not configured: {settings.transfer_resolver_provider_name}")

    payout_resolver_provider = ProviderFactory.get_resolver_for_flow("payout")
    if payout_resolver_provider is None:
        raise RuntimeError(f"Payout resolver provider is not configured: {settings.payout_resolver_provider_name}")

    direct_debit_provider = ProviderFactory.get_direct_debit_provider()
    if direct_debit_provider is None:
        raise RuntimeError(
            f"Account provider direct-debit capability is not configured: {settings.account_provider_name}"
        )

    bill_provider = ProviderFactory.get_bill_provider()
    if bill_provider is None:
        raise RuntimeError("Bill provider is not configured")

    return ChatRuntimeProviders(
        bank_data_provider=bank_data_provider,
        resolver_provider=resolver_provider,
        payout_resolver_provider=payout_resolver_provider,
        direct_debit_provider=direct_debit_provider,
        bill_provider=bill_provider,
    )
