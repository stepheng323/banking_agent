"""Explicit provider factory for payout, bank data, resolver and bills."""

from typing import Literal

from shared.clients.abstractions.banking import BankDataProvider
from shared.clients.abstractions.bill import BillPaymentProvider
from shared.clients.abstractions.payment import PayoutProvider
from shared.clients.abstractions.resolution import AccountResolverProvider
from shared.clients.providers.flutterwave.bill import FlutterwaveBillsClient

ResolverFlow = Literal["transfer", "beneficiary", "payout", "bootstrap"]


class ProviderFactory:
    """Factory for explicit provider categories and flow routing."""

    RESOLVER_FLOW_MAP: dict[ResolverFlow, str] = {
        "transfer": "mono",
        "beneficiary": "mono",
        "payout": "flutterwave",
        "bootstrap": "mono",
    }

    @staticmethod
    def create_payout_provider(provider_name: str) -> PayoutProvider | None:
        if provider_name == "flutterwave":
            try:
                from shared.clients.providers.flutterwave.payment import FlutterwavePaymentProvider

                return FlutterwavePaymentProvider()
            except ValueError:
                return None
        if provider_name == "mono":
            try:
                from shared.clients.providers.mono.payment import MonoPaymentProvider

                return MonoPaymentProvider()
            except (ValueError, ImportError):
                return None
        return None

    @staticmethod
    def create_bank_data_provider(provider_name: str) -> BankDataProvider | None:
        if provider_name == "mono":
            try:
                from shared.clients.providers.mono.banking import MonoBankingProvider

                return MonoBankingProvider()
            except (ValueError, ImportError):
                return None
        return None

    @staticmethod
    def create_resolver_provider(provider_name: str) -> AccountResolverProvider | None:
        if provider_name == "mono":
            try:
                from shared.clients.providers.mono.resolver import MonoResolverProvider

                return MonoResolverProvider()
            except (ValueError, ImportError):
                return None
        if provider_name == "flutterwave":
            try:
                from shared.clients.providers.flutterwave.resolver import FlutterwaveResolverProvider

                return FlutterwaveResolverProvider()
            except (ValueError, ImportError):
                return None
        return None

    @staticmethod
    def get_payout_provider(name: str = "flutterwave") -> PayoutProvider | None:
        provider = ProviderFactory.create_payout_provider(name)
        if provider and provider.is_available:
            return provider
        return None

    @staticmethod
    def get_bank_data_provider(name: str = "mono") -> BankDataProvider | None:
        provider = ProviderFactory.create_bank_data_provider(name)
        if provider and provider.is_available:
            return provider
        return None

    @staticmethod
    def get_resolver_for_flow(flow: ResolverFlow) -> AccountResolverProvider | None:
        provider_name = ProviderFactory.RESOLVER_FLOW_MAP.get(flow)
        if not provider_name:
            return None
        provider = ProviderFactory.create_resolver_provider(provider_name)
        if provider and provider.is_available:
            return provider
        return None

    @staticmethod
    def get_bill_provider(name: str = "flutterwave") -> BillPaymentProvider | None:
        if name == "flutterwave":
            try:
                return FlutterwaveBillsClient()
            except ValueError:
                return None
        return None
