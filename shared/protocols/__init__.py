"""Protocol definitions for agent services."""

from shared.protocols.services import (
    AccountManagementServiceProtocol,
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
    "AccountManagementServiceProtocol",
    "SupportServiceProtocol",
    "FAQServiceProtocol",
]
