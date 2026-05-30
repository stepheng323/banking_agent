"""Mono implementation of PayoutProvider."""

from typing import Any

from shared.clients.abstractions.payment import PayoutProvider
from shared.clients.providers.mono.client import MonoClient
from shared.config.settings import settings


class MonoPaymentProvider(PayoutProvider):
    """
    Mono implementation of PayoutProvider.

    Mono payouts are not currently supported in this system.
    """

    def __init__(self, mono_client: MonoClient | None = None):
        self._client = mono_client or MonoClient()

    @property
    def provider_name(self) -> str:
        return "mono"

    @property
    def is_available(self) -> bool:
        return bool(settings.mono_api_key)

    async def initiate_transfer(
        self,
        amount: float,
        recipient_account_number: str,
        recipient_bank_code: str,
        sender_account_number: str | None = None,
        narration: str | None = None,
        currency: str = "NGN",
        reference: str | None = None,
    ) -> dict[str, Any]:
        """Initiate transfer (not yet supported by Mono adapter)."""
        del reference
        raise NotImplementedError("Mono transfers not yet supported via PayoutProvider interface")

    async def get_transfer_status(self, transaction_id: str) -> dict[str, Any]:
        """Get transfer status (not yet supported by Mono adapter)."""
        raise NotImplementedError("Mono transfer status not yet supported via PayoutProvider interface")
