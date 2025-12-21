"""Direct debit provider abstraction.

Provides a provider-agnostic interface for pulling funds from user bank accounts.
"""
from shared.clients.direct_debit.base import (
    DirectDebitProvider,
    DebitResult,
    DebitStatus,
    BalanceResult,
    AccountInfo,
)
from shared.clients.direct_debit.factory import (
    DirectDebitProviderFactory,
    get_direct_debit_provider,
)

__all__ = [
    "DirectDebitProvider",
    "DebitResult",
    "DebitStatus",
    "BalanceResult",
    "AccountInfo",
    "DirectDebitProviderFactory",
    "get_direct_debit_provider",
]
