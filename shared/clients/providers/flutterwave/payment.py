"""Flutterwave API client for payment services using v3 API."""

import uuid
from typing import Any

import structlog

from shared.clients.abstractions.payment import PaymentProvider
from shared.clients.providers.flutterwave.client import FlutterwaveClient

logger = structlog.get_logger(__name__)


FLUTTERWAVE_TEST_ACCOUNT = "0690000032"
FLUTTERWAVE_TEST_BANK_CODE = "044"  # Access Bank


class FlutterwavePaymentProvider(PaymentProvider):
    """Flutterwave payment service provider implementation using v3 API."""

    def __init__(
        self,
        client: FlutterwaveClient | None = None,
    ):
        """
        Initialize provider.

        Args:
            client: Optional injected FlutterwaveClient
        """
        self._client = client or FlutterwaveClient()
        self.use_sandbox = self._client.use_sandbox

    @property
    def provider_name(self) -> str:
        """Return the name of the provider."""
        return "flutterwave"

    @property
    def is_available(self) -> bool:
        """Check if Flutterwave provider is properly configured."""
        return self._client.is_configured

    @property
    def supports_transfers(self) -> bool:
        """Flutterwave supports transfer operations."""
        return True

    @property
    def supports_status_checks(self) -> bool:
        """Flutterwave supports transaction status checks."""
        return True

    @property
    def supports_bank_list(self) -> bool:
        """Flutterwave supports fetching bank lists."""
        return True

    def _error_response(self, error: str, **kwargs) -> dict[str, Any]:
        """Build a standardized error response."""
        return {
            "success": False,
            "error": error,
            "provider": self.provider_name,
            **kwargs,
        }

    def _success_response(self, **kwargs) -> dict[str, Any]:
        """Build a standardized success response."""
        return {
            "success": True,
            "provider": self.provider_name,
            **kwargs,
        }

    async def get_banks(self, country: str = "NG") -> dict[str, Any]:
        """Fetch list of supported banks from Flutterwave."""
        result = await self._client.request("GET", f"/v3/banks/{country}")

        if result["success"]:
            banks = result.get("data", [])
            return self._success_response(banks=banks, count=len(banks))

        return self._error_response(result.get("error", "Failed to fetch banks"), banks=[], count=0)

    async def initiate_transfer(
        self,
        amount: float,
        recipient_account_number: str,
        recipient_bank_code: str,
        sender_account_number: str | None = None,
        narration: str | None = None,
        currency: str = "NGN",
    ) -> dict[str, Any]:
        """
        Initiate a bank transfer via Flutterwave.

        TODO: Implement Flutterwave transfer API integration.
        This is a placeholder implementation that returns a mock success response.
        """
        # Generate professional transaction ID: FP-YYYYMMDD-XXXX
        from datetime import datetime

        date_part = datetime.utcnow().strftime("%Y%m%d")
        random_part = uuid.uuid4().hex[:8].upper()
        transaction_id = f"FP-{date_part}-{random_part}"

        msg = f"🔍 Placeholder transfer initiated: {amount} {currency} to {recipient_account_number}"
        print(f"{msg} ({recipient_bank_code})")
        print(f"   Transaction ID: {transaction_id}")

        return {
            "success": True,
            "transaction_id": transaction_id,
            "status": "success",
            "amount": amount,
            "recipient_account_number": recipient_account_number,
            "recipient_bank_code": recipient_bank_code,
            "currency": currency,
            "provider": self.provider_name,
        }

    async def get_transfer_status(self, transaction_id: str) -> dict[str, Any]:
        """
        Get transfer status from Flutterwave.

        TODO: Implement Flutterwave status check API integration.
        """
        raise NotImplementedError("Flutterwave status check not yet implemented")

    async def resolve_account(
        self, account_number: str, bank_code: str, currency: str = "NGN", max_retries: int = 3
    ) -> dict[str, Any]:
        """Resolve bank account details using Flutterwave Account Resolution API."""
        original_account = account_number
        original_bank = bank_code

        # In sandbox mode, swap to test account for API call but return original account info
        if self.use_sandbox:
            logger.info(
                "sandbox_mode_swap",
                original_account=account_number,
                test_account=FLUTTERWAVE_TEST_ACCOUNT,
            )
            account_number = FLUTTERWAVE_TEST_ACCOUNT
            bank_code = FLUTTERWAVE_TEST_BANK_CODE

        account_info = {"account_number": original_account, "bank_code": original_bank}
        payload = {"account_number": account_number, "account_bank": bank_code}

        result = await self._client.request("POST", "/v3/accounts/resolve", payload=payload, max_retries=max_retries)

        if result["success"]:
            data = result.get("data", {})
            account_name = data.get("account_name", "").strip()
            if account_name:
                # Return original account info, not the test account
                return self._success_response(
                    account_name=account_name,
                    account_number=original_account,
                    bank_code=original_bank,
                )
            return self._error_response("Account name not found", **account_info)

        return self._error_response(result.get("error", "Account resolution failed"), **account_info)
