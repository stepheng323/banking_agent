"""Provider abstractions - abstract base classes for payment providers."""

from shared.clients.abstractions.banking import (
    AccountData,
    BalanceData,
    BankDataProvider,
    BvnLookupResult,
    BvnVerificationResult,
    TransactionData,
    TransactionPageData,
)
from shared.clients.abstractions.bill import BillPaymentProvider
from shared.clients.abstractions.direct_debit import (
    AccountInfo,
    BalanceResult,
    DebitResult,
    DebitStatus,
    DirectDebitProvider,
)
from shared.clients.abstractions.messaging import MessageResult, MessagingClient
from shared.clients.abstractions.payment import PayoutProvider
from shared.clients.abstractions.resolution import (
    AccountResolutionResult,
    AccountResolverProvider,
    BankListResult,
    BankRecord,
    ResolvedAccount,
)

__all__ = [
    "BankDataProvider",
    "AccountData",
    "BalanceData",
    "TransactionData",
    "TransactionPageData",
    "BvnLookupResult",
    "BvnVerificationResult",
    "DirectDebitProvider",
    "DebitResult",
    "DebitStatus",
    "BalanceResult",
    "AccountInfo",
    "PayoutProvider",
    "AccountResolverProvider",
    "BankRecord",
    "BankListResult",
    "ResolvedAccount",
    "AccountResolutionResult",
    "BillPaymentProvider",
    "MessagingClient",
    "MessageResult",
]
