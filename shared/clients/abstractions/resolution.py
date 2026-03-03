"""Account resolution and bank directory abstraction."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(slots=True, frozen=True)
class BankRecord:
    """Canonical bank record used for lookup/cache."""

    code: str
    name: str


@dataclass(slots=True, frozen=True)
class ResolvedAccount:
    """Canonical resolved account details."""

    account_name: str
    account_number: str
    bank_code: str


@dataclass(slots=True)
class BankListResult:
    """Result of fetching bank list from a resolver provider."""

    success: bool
    banks: list[BankRecord] = field(default_factory=list)
    error: str | None = None
    provider: str | None = None


@dataclass(slots=True)
class AccountResolutionResult:
    """Result of resolving account details."""

    success: bool
    account: ResolvedAccount | None = None
    error: str | None = None
    provider: str | None = None


class AccountResolverProvider(ABC):
    """Abstract interface for account resolution and bank directory."""

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
    async def get_banks(self, country: str = "NG") -> BankListResult:
        """Fetch supported banks for given country."""
        raise NotImplementedError

    @abstractmethod
    async def resolve_account(self, account_number: str, bank_code: str) -> AccountResolutionResult:
        """Resolve account details for account number + bank code."""
        raise NotImplementedError
