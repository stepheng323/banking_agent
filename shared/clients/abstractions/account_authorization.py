"""Account authorization provider abstractions.

This contract covers provider flows that create a customer profile and create
the bank-account mandate/authorization required before direct debit.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class ProviderCustomer:
    """Canonical customer profile returned by an account authorization provider."""

    id: str
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class ProviderTransferDestination:
    """Provider destination the user must fund/authorize to complete a mandate."""

    bank_name: str
    account_number: str


@dataclass(slots=True, frozen=True)
class ProviderMandate:
    """Canonical mandate/authorization response."""

    id: str
    reference: str | None = None
    status: str | None = None
    transfer_destinations: list[ProviderTransferDestination] = field(default_factory=list)
    raw_response: dict[str, Any] = field(default_factory=dict)


class AccountAuthorizationProvider(ABC):
    """Interface for provider-specific account authorization lifecycle calls."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return provider name."""
        raise NotImplementedError

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """Return whether provider is configured and available."""
        raise NotImplementedError

    @abstractmethod
    async def create_customer(
        self,
        *,
        first_name: str,
        last_name: str,
        phone: str,
        email: str,
        address: str,
        identity_number: str,
        identity_type: str,
    ) -> ProviderCustomer:
        """Create or register the customer with the provider."""
        raise NotImplementedError

    @abstractmethod
    async def create_mandate(
        self,
        *,
        customer_id: str,
        account_number: str,
        bank_code: str,
        maximum_debit_amount_minor: int,
        reference: str,
        start_date: str,
        end_date: str,
    ) -> ProviderMandate:
        """Create a mandate/authorization for debiting the account."""
        raise NotImplementedError
