"""Mono implementation of PaymentProvider."""

from typing import Any

from shared.clients.abstractions.payment import PaymentProvider
from shared.clients.providers.mono.client import MonoClient
from shared.config.settings import settings


class MonoPaymentProvider(PaymentProvider):
    """
    Mono implementation of PaymentProvider.

    Adapts MonoClient to the generic PaymentProvider interface.
    Primarily used for account resolution and fetching banks.
    """

    def __init__(self, mono_client: MonoClient | None = None):
        self._client = mono_client or MonoClient()

    @property
    def provider_name(self) -> str:
        return "mono"

    @property
    def is_available(self) -> bool:
        return bool(settings.mono_api_key)

    @property
    def supports_bank_list(self) -> bool:
        return True

    async def get_banks(self, country: str = "NG") -> dict[str, Any]:
        """Fetch list of supported banks from Mono."""
        try:
            banks = await self._client.get_banks()
            return {
                "success": True,
                "banks": banks,
                "count": len(banks),
                "provider": self.provider_name,
            }
        except Exception as e:
            return {
                "success": False,
                "banks": [],
                "count": 0,
                "error": str(e),
                "provider": self.provider_name,
            }

    async def resolve_account(self, account_number: str, bank_code: str, currency: str = "NGN") -> dict[str, Any]:
        """Resolve bank account details using Mono."""
        try:
            lookup = await self._client.lookup_account_number(account_number, bank_code)
            if lookup:
                return {
                    "success": True,
                    "account_name": lookup.name,
                    "account_number": lookup.account_number,
                    "bank_code": lookup.bank.code,
                    "provider": self.provider_name,
                }
            return {
                "success": False,
                "error": "Account not found",
                "provider": self.provider_name,
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "provider": self.provider_name,
            }

    async def initiate_transfer(
        self,
        amount: float,
        recipient_account_number: str,
        recipient_bank_code: str,
        sender_account_number: str | None = None,
        narration: str | None = None,
        currency: str = "NGN",
    ) -> dict[str, Any]:
        """Initiate transfer (not yet supported by Mono adapter)."""
        raise NotImplementedError("Mono transfers not yet supported via PaymentProvider interface")

    async def get_transfer_status(self, transaction_id: str) -> dict[str, Any]:
        """Get transfer status (not yet supported by Mono adapter)."""
        raise NotImplementedError("Mono transfer status not yet supported via PaymentProvider interface")
