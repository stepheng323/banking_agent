"""Explicit provider factory for payout, bank data, resolver and bills."""

from typing import Literal

from shared.clients.abstractions.account_authorization import AccountAuthorizationProvider
from shared.clients.abstractions.banking import BankDataProvider
from shared.clients.abstractions.bill import BillPaymentProvider
from shared.clients.abstractions.direct_debit import DirectDebitProvider
from shared.clients.abstractions.payment import PayoutProvider
from shared.clients.abstractions.resolution import AccountResolverProvider
from shared.config.settings import settings

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
    def _normalize_provider_name(provider_name: str | None, fallback: str) -> str:
        return (provider_name or fallback).strip().lower()

    @staticmethod
    def create_direct_debit_provider(provider_name: str) -> DirectDebitProvider | None:
        if provider_name == "mock":
            try:
                from shared.clients.providers.mock.direct_debit import MockDirectDebitProvider

                return MockDirectDebitProvider()
            except (ValueError, ImportError):
                return None
        if provider_name == "mono":
            try:
                from shared.clients.providers.mono.direct_debit import MonoDirectDebitProvider

                return MonoDirectDebitProvider()
            except (ValueError, ImportError):
                return None
        return None

    @staticmethod
    def create_account_authorization_provider(provider_name: str) -> AccountAuthorizationProvider | None:
        if provider_name == "mono":
            try:
                from shared.clients.providers.mono.account_authorization import MonoAccountAuthorizationProvider

                return MonoAccountAuthorizationProvider()
            except (ValueError, ImportError):
                return None
        return None

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
    def get_payout_provider(name: str | None = None) -> PayoutProvider | None:
        provider_name = ProviderFactory._normalize_provider_name(name, settings.payout_provider_name)
        provider = ProviderFactory.create_payout_provider(provider_name)
        if provider and provider.is_available:
            return provider
        return None

    @staticmethod
    def get_direct_debit_provider(name: str | None = None) -> DirectDebitProvider | None:
        provider_name = ProviderFactory._normalize_provider_name(name, settings.account_provider_name)
        return ProviderFactory.create_direct_debit_provider(provider_name)

    @staticmethod
    def get_account_authorization_provider(name: str | None = None) -> AccountAuthorizationProvider | None:
        provider_name = ProviderFactory._normalize_provider_name(name, settings.account_provider_name)
        provider = ProviderFactory.create_account_authorization_provider(provider_name)
        if provider and provider.is_available:
            return provider
        return None

    @staticmethod
    def get_bank_data_provider(name: str | None = None) -> BankDataProvider | None:
        provider_name = ProviderFactory._normalize_provider_name(name, settings.account_provider_name)
        provider = ProviderFactory.create_bank_data_provider(provider_name)
        if provider and provider.is_available:
            return provider
        return None

    @staticmethod
    def get_resolver_for_flow(flow: ResolverFlow) -> AccountResolverProvider | None:
        provider_name = settings.resolver_provider_for_flow(flow) or ProviderFactory.RESOLVER_FLOW_MAP.get(flow)
        if not provider_name:
            return None
        provider = ProviderFactory.create_resolver_provider(provider_name)
        if provider and provider.is_available:
            return provider
        return None

    @staticmethod
    def create_bill_provider(provider_name: str) -> BillPaymentProvider | None:
        if provider_name == "flutterwave":
            try:
                from shared.clients.providers.flutterwave.bill import FlutterwaveBillsClient

                return FlutterwaveBillsClient()
            except ValueError:
                return None
        return None

    @staticmethod
    def get_bill_provider(name: str | None = None) -> BillPaymentProvider | None:
        provider_name = ProviderFactory._normalize_provider_name(name, settings.bill_provider_name)
        provider = ProviderFactory.create_bill_provider(provider_name)
        if provider and provider.is_available:
            return provider
        return None
