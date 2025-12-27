"""Flutterwave API client for payment services using v3 API."""

import asyncio
import uuid
from typing import Any

import httpx
import structlog

from shared.clients.abstractions.payment import PaymentProvider
from shared.config.settings import settings

logger = structlog.get_logger(__name__)


FLUTTERWAVE_BASE_URL = "https://api.flutterwave.com"

# Flutterwave sandbox test account (only this works in dev mode)
FLUTTERWAVE_TEST_ACCOUNT = "0690000032"
FLUTTERWAVE_TEST_BANK_CODE = "044"  # Access Bank


class FlutterwaveClient(PaymentProvider):
    """Flutterwave payment service provider implementation using v3 API."""

    def __init__(
        self,
        secret_key: str | None = None,
        use_sandbox: bool | None = None,
    ):
        """
        Initialize Flutterwave client with v3 secret key.

        Args:
            secret_key: v3 API secret key (or from FLUTTERWAVE_SECRET_KEY env var)
            use_sandbox: Whether to use sandbox environment

        Raises:
            ValueError: If secret key is not configured
        """
        self.secret_key = secret_key or getattr(settings, "flutterwave_secret_key", None)

        if not self.secret_key:
            raise ValueError(
                "Flutterwave secret key not configured. "
                "Set FLUTTERWAVE_SECRET_KEY environment variable."
            )

        self.use_sandbox = (
            use_sandbox
            if use_sandbox is not None
            else getattr(settings, "flutterwave_use_sandbox", False)
        )
        self.base_url = FLUTTERWAVE_BASE_URL

    @property
    def provider_name(self) -> str:
        """Return the name of the provider."""
        return "flutterwave"

    @property
    def is_available(self) -> bool:
        """Check if Flutterwave provider is properly configured."""
        return bool(self.secret_key)

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

    def _get_headers(self) -> dict[str, str]:
        """Get request headers with secret key authentication."""
        return {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
            "accept": "application/json",
        }

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

    def _extract_error_message(self, response: httpx.Response) -> str:
        """Extract error message from HTTP response."""
        try:
            body = response.json()
            error_data = body.get("error")
            if isinstance(error_data, dict):
                return error_data.get("message", f"HTTP {response.status_code}")
            elif isinstance(error_data, str):
                return error_data
            return body.get("message") or f"HTTP {response.status_code}"
        except Exception:
            return f"HTTP {response.status_code}"

    async def _request(
        self,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 10.0,
        max_retries: int = 1,
    ) -> dict[str, Any]:
        """
        Unified HTTP request handler with error handling.

        Args:
            method: HTTP method ("GET" or "POST")
            endpoint: API endpoint (will be appended to base_url)
            payload: Request body for POST requests
            timeout: Request timeout in seconds
            max_retries: Number of retry attempts

        Returns:
            Dictionary with:
                - success: bool
                - data: response data if successful
                - status_code: HTTP status code
                - error: error message if failed
        """
        url = f"{self.base_url}{endpoint}"
        headers = self._get_headers()
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    if method.upper() == "GET":
                        response = await client.get(url, headers=headers)
                    else:
                        response = await client.post(url, headers=headers, json=payload)

                    result = response.json()

                    if response.status_code == 200 and result.get("status") == "success":
                        return {
                            "success": True,
                            "data": result.get("data", result),
                            "status_code": response.status_code,
                            "message": result.get("message"),
                        }

                    if response.status_code == 401:
                        logger.error("api_auth_failed", endpoint=endpoint)
                        return {
                            "success": False,
                            "error": "Authentication failed. Check your secret key.",
                            "status_code": 401,
                        }

                    if response.status_code == 400:
                        error_msg = self._extract_error_message(response)
                        return {
                            "success": False,
                            "error": error_msg,
                            "status_code": 400,
                        }

                    error_msg = self._extract_error_message(response)
                    if attempt < max_retries:
                        logger.warning(
                            "api_error_retrying",
                            endpoint=endpoint,
                            status=response.status_code,
                            attempt=attempt,
                            error=error_msg,
                        )
                        await asyncio.sleep(1 * attempt)
                        continue

                    return {
                        "success": False,
                        "error": error_msg,
                        "status_code": response.status_code,
                    }

            except httpx.ConnectError as e:
                last_error = str(e)
                logger.warning("api_connection_error", endpoint=endpoint, attempt=attempt)
                if attempt < max_retries:
                    await asyncio.sleep(1 * attempt)
                    continue
                return {
                    "success": False,
                    "error": "Failed to connect to service. Please try again.",
                    "status_code": 0,
                }

            except Exception as e:
                last_error = str(e)
                logger.error("api_unexpected_error", endpoint=endpoint, error=str(e))
                if attempt < max_retries:
                    await asyncio.sleep(1 * attempt)
                    continue
                return {
                    "success": False,
                    "error": f"Unexpected error: {str(e)}",
                    "status_code": 0,
                }

        return {
            "success": False,
            "error": f"Failed after {max_retries} attempts: {last_error}",
            "status_code": 0,
        }

    async def fetch_banks(self, country: str = "NG") -> dict[str, Any]:
        """Fetch list of supported banks from Flutterwave."""
        result = await self._request("GET", f"/v3/banks/{country}")

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
        transaction_id = f"mock_txn_{uuid.uuid4().hex[:16]}"

        msg = (
            f"🔍 Placeholder transfer initiated: {amount} {currency} to {recipient_account_number}"
        )
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

        result = await self._request(
            "POST", "/v3/accounts/resolve", payload=payload, max_retries=max_retries
        )

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

        return self._error_response(
            result.get("error", "Account resolution failed"), **account_info
        )
