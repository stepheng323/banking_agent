"""Protocol definitions for agent services."""

from shared.protocols.services import (
    AccountServiceProtocol,
    AirtimeServiceProtocol,
    DataServiceProtocol,
    FAQServiceProtocol,
    QueryServiceProtocol,
    SupportServiceProtocol,
    TransferServiceProtocol,
)

__all__ = [
    "TransferServiceProtocol",
    "AirtimeServiceProtocol",
    "DataServiceProtocol",
    "QueryServiceProtocol",
    "AccountServiceProtocol",
    "SupportServiceProtocol",
    "FAQServiceProtocol",
]
