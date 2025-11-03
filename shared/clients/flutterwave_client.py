"""Flutterwave API client for payment services."""
import asyncio
import time
from typing import Any, Dict, Optional

import httpx
from shared.config.settings import settings
from shared.clients.payment_provider import PaymentProvider


FLUTTERWAVE_BASE_URL = "https://api.flutterwave.com"
FLUTTERWAVE_SANDBOX_URL = "https://developersandbox-api.flutterwave.com"
FLUTTERWAVE_TOKEN_URL = "https://idp.flutterwave.com/realms/flutterwave/protocol/openid-connect/token"


class FlutterwaveClient(PaymentProvider):
    """Flutterwave payment service provider implementation."""

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        use_sandbox: Optional[bool] = None,
    ):
        """
        Initialize Flutterwave client with OAuth2 credentials.

        Args:
            client_id: OAuth2 client ID (or from FLUTTERWAVE_CLIENT_ID env var)
            client_secret: OAuth2 client secret (or from FLUTTERWAVE_CLIENT_SECRET env var)
            use_sandbox: Whether to use sandbox environment

        Raises:
            ValueError: If OAuth2 credentials are not configured
        """
        self.client_id = client_id or getattr(
            settings, "flutterwave_client_id", None)
        self.client_secret = client_secret or getattr(
            settings, "flutterwave_client_secret", None)

        if not self.client_id or not self.client_secret:
            raise ValueError(
                "Flutterwave OAuth2 credentials not configured. "
                "Set FLUTTERWAVE_CLIENT_ID and FLUTTERWAVE_CLIENT_SECRET environment variables."
            )

        self.use_sandbox = use_sandbox if use_sandbox is not None else getattr(
            settings, "flutterwave_use_sandbox", False
        )
        self.base_url = FLUTTERWAVE_SANDBOX_URL if self.use_sandbox else FLUTTERWAVE_BASE_URL

        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._refresh_token: Optional[str] = None
        self._token_refresh_task: Optional[asyncio.Task] = None
        self._is_shutting_down: bool = False

    @property
    def provider_name(self) -> str:
        """Return the name of the provider."""
        return "flutterwave"

    @property
    def is_available(self) -> bool:
        """Check if Flutterwave provider is properly configured."""
        return bool(self.client_id and self.client_secret)

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

    @property
    def access_token(self) -> Optional[str]:
        """Get the current access token (may be expired)."""
        return self._access_token

    async def ensure_valid_token(self) -> str:
        """
        Ensure we have a valid access token, refreshing if necessary.

        Returns:
            Valid access token
        """
        return await self._get_access_token()

    async def warm_up_token(self) -> None:
        """
        Proactively fetch and cache the OAuth token at startup.
        Also starts a background task to keep the token fresh.
        """
        try:
            await self._get_access_token()
            print("✅ Flutterwave OAuth token warmed up and ready")

            if not self._token_refresh_task or self._token_refresh_task.done():
                self._token_refresh_task = asyncio.create_task(
                    self._token_refresh_background_task()
                )
                print("   🔄 Background token refresh task started")
        except Exception as e:
            print(f"⚠️  Failed to warm up token: {e}")

    async def shutdown(self) -> None:
        """
        Gracefully shutdown the client, canceling background tasks.
        """
        self._is_shutting_down = True
        if self._token_refresh_task and not self._token_refresh_task.done():
            self._token_refresh_task.cancel()
            try:
                await self._token_refresh_task
            except asyncio.CancelledError:
                pass
        print("✅ Flutterwave client shut down gracefully")

    async def _token_refresh_background_task(self) -> None:
        """
        Background task to proactively refresh the OAuth token before it expires.
        Runs continuously until shutdown.
        """
        while not self._is_shutting_down:
            try:
                current_time = time.time()

                if current_time >= (self._token_expires_at - 120):
                    print("🔄 Proactively refreshing OAuth token...")
                    await self._get_access_token()
                    print("✅ OAuth token refreshed in background")

                await asyncio.sleep(60)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️  Background token refresh error: {e}")
                await asyncio.sleep(120)

    async def fetch_banks(self, country: str = "NG") -> Dict[str, Any]:
        """
        Fetch list of supported banks from Flutterwave.

        Args:
            country: Country code (e.g., "NG" for Nigeria, "GH" for Ghana)

        Returns:
            Dictionary with:
                - success: bool
                - banks: List[Dict[str, str]] with id, code, and name
                - count: int (number of banks)
                - error: str (if failed)
                - provider: str
        """
        try:
            token = await self._get_access_token()

            url = f"{self.base_url}/banks"
            params = {"country": country}
            headers = {
                "accept": "application/json",
                "authorization": f"Bearer {token}"
            }

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    url,
                    params=params,
                    headers=headers
                )
                response.raise_for_status()

                data = response.json()
                if data.get("status") == "success":
                    banks = data.get("data", [])
                    return {
                        "success": True,
                        "banks": banks,
                        "count": len(banks),
                        "provider": self.provider_name
                    }

                error_msg = (
                    data.get("message")
                    or data.get("error")
                    or "Failed to fetch banks"
                )
                return {
                    "success": False,
                    "banks": [],
                    "count": 0,
                    "error": error_msg,
                    "provider": self.provider_name
                }

        except httpx.HTTPStatusError as e:
            error_msg = f"HTTP {e.response.status_code}"
            try:
                error_body = e.response.json()
                error_msg = error_body.get("message", error_msg)
            except Exception:
                pass

            return {
                "success": False,
                "banks": [],
                "count": 0,
                "error": f"API error: {error_msg}",
                "provider": self.provider_name
            }

        except Exception as e:
            return {
                "success": False,
                "banks": [],
                "count": 0,
                "error": f"Failed to fetch banks: {str(e)}",
                "provider": self.provider_name
            }

    async def initiate_transfer(
        self,
        amount: float,
        recipient_account_number: str,
        recipient_bank_code: str,
        sender_account_number: Optional[str] = None,
        narration: Optional[str] = None,
        currency: str = "NGN",
    ) -> Dict[str, Any]:
        """
        Initiate a bank transfer via Flutterwave.

        TODO: Implement Flutterwave transfer API integration.
        """
        # TODO: Implement Flutterwave transfer initiation
        raise NotImplementedError(
            "Flutterwave transfer initiation not yet implemented")

    async def get_transfer_status(
        self, transaction_id: str
    ) -> Dict[str, Any]:
        """
        Get transfer status from Flutterwave.

        TODO: Implement Flutterwave status check API integration.
        """
        # TODO: Implement Flutterwave status check
        raise NotImplementedError(
            "Flutterwave status check not yet implemented")

    async def _get_access_token(self) -> str:
        """
        Get a valid access token, refreshing if necessary.

        Tokens expire in 10 minutes (600 seconds). We refresh when less than 60 seconds remain.
        This method is called both on-demand and proactively by background task.
        """
        current_time = time.time()

        # If token is still valid with at least 60 seconds remaining, return it
        if self._access_token and current_time < (self._token_expires_at - 60):
            return self._access_token

        # Need to fetch new token
        is_refresh = bool(self._access_token)
        if is_refresh:
            print("🔄 Refreshing Flutterwave OAuth2 token...")
        else:
            print("🔑 Fetching Flutterwave OAuth2 token...")

        token_data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    FLUTTERWAVE_TOKEN_URL,
                    data=token_data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                response.raise_for_status()
                token_response = response.json()

                access_token = token_response.get("access_token")
                expires_in = token_response.get(
                    "expires_in", 600)
                self._refresh_token = token_response.get("refresh_token")

                if not access_token:
                    raise ValueError("No access_token in OAuth2 response")

                self._access_token = access_token
                self._token_expires_at = current_time + expires_in

                # Calculate actual expiry time for logging
                expires_in_minutes = expires_in / 60
                if is_refresh:
                    print(
                        f"✅ OAuth2 token refreshed (expires in {expires_in_minutes:.1f} minutes)")
                else:
                    print(
                        f"✅ OAuth2 token obtained (expires in {expires_in_minutes:.1f} minutes)")

                return access_token

        except httpx.HTTPStatusError as e:
            error_msg = f"Failed to obtain OAuth2 token: {e.response.status_code}"
            try:
                error_body = e.response.json()
                error_msg += f" - {error_body.get('error_description', error_body.get('error', ''))}"
            except Exception:
                pass
            print(f"❌ {error_msg}")
            raise ValueError(error_msg) from e
        except Exception as e:
            print(f"❌ Error fetching OAuth2 token: {e}")
            raise

    async def _get_headers(self) -> Dict[str, str]:
        """
        Get request headers with OAuth2 authentication.

        Returns headers with Bearer token from OAuth2 flow.
        """
        token = await self._get_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def resolve_account(
        self, account_number: str, bank_code: str, currency: str = "NGN", max_retries: int = 3
    ) -> Dict[str, Any]:
        """
        Resolve bank account details using Flutterwave Account Resolution API.

        Args:
            account_number: The bank account number to verify
            bank_code: The bank code (e.g., "058" for GTBank, "011" for First Bank)
            currency: Currency code (default: "NGN")
            max_retries: Maximum number of retry attempts on failure

        Returns:
            Dictionary with:
                - success: bool
                - account_name: str (account holder name) if successful
                - account_number: str (normalized account number)
                - bank_code: str
                - error: str if failed
        """
        url = f"{self.base_url}/banks/account-resolve"
        payload = {
            "account": {
                "code": bank_code,
                "number": account_number,
            },
            "currency": currency,
        }
        headers = await self._get_headers()

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                    result = response.json()
                    if result.get("status") == "success":
                        data = result.get("data", {})
                        account_name = data.get("account_name", "").strip()
                        if account_name:
                            return {
                                "success": True,
                                "account_name": account_name,
                                "account_number": data.get("account_number", account_number),
                                "bank_code": data.get("bank_code", bank_code),
                                "provider": self.provider_name,
                            }

                    error_msg = (
                        result.get("message")
                        or result.get("error")
                        or result.get("errorMessage")
                        or "Account resolution failed"
                    )
                    return {
                        "success": False,
                        "error": error_msg,
                        "account_number": account_number,
                        "bank_code": bank_code,
                        "provider": self.provider_name,
                    }

            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code == 401:
                    print("❌ Flutterwave API Authentication Failed (401)")

                    if attempt == 1:
                        print("   Token may have expired, attempting refresh...")
                        self._access_token = None
                        self._token_expires_at = 0.0
                        headers = await self._get_headers()
                        continue

                    print(
                        "   Check your FLUTTERWAVE_CLIENT_ID and FLUTTERWAVE_CLIENT_SECRET")

                    return {
                        "success": False,
                        "error": "Authentication failed. Please check your OAuth2 credentials.",
                        "account_number": account_number,
                        "bank_code": bank_code,
                        "provider": self.provider_name,
                    }
                elif e.response.status_code == 400:
                    try:
                        error_data = e.response.json()
                        error_msg = error_data.get(
                            "message", "Invalid account details")
                        return {
                            "success": False,
                            "error": error_msg,
                            "account_number": account_number,
                            "bank_code": bank_code,
                            "provider": self.provider_name,
                        }
                    except Exception:
                        return {
                            "success": False,
                            "error": "Invalid account number or bank code",
                            "account_number": account_number,
                            "bank_code": bank_code,
                            "provider": self.provider_name,
                        }
                elif attempt < max_retries:
                    print(
                        f"⚠️  Flutterwave API {e.response.status_code} error (attempt {attempt}/{max_retries}), retrying..."
                    )
                    await asyncio.sleep(1 * attempt)
                else:
                    print(f"❌ Flutterwave API error: {e.response.status_code}")
                    try:
                        error_body = e.response.json()
                        error_msg = error_body.get("message", "Unknown error")
                    except Exception:
                        error_msg = str(e)
                    return {
                        "success": False,
                        "error": f"API error: {error_msg}",
                        "account_number": account_number,
                        "bank_code": bank_code,
                        "provider": self.provider_name,
                    }

            except httpx.ConnectError as e:
                last_error = e
                if attempt < max_retries:
                    print(
                        f"⚠️  Connection error (attempt {attempt}/{max_retries}), retrying..."
                    )
                    await asyncio.sleep(1 * attempt)
                else:
                    print(f"❌ Failed to connect to Flutterwave API: {e}")
                    return {
                        "success": False,
                        "error": "Failed to connect to account verification service. Please try again.",
                        "account_number": account_number,
                        "bank_code": bank_code,
                        "provider": self.provider_name,
                    }

            except Exception as e:
                last_error = e
                print(f"❌ Unexpected error resolving account: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(1 * attempt)
                else:
                    return {
                        "success": False,
                        "error": f"Unexpected error: {str(e)}",
                        "account_number": account_number,
                        "bank_code": bank_code,
                        "provider": self.provider_name,
                    }

        return {
            "success": False,
            "error": f"Failed after {max_retries} attempts: {str(last_error)}",
            "account_number": account_number,
            "bank_code": bank_code,
            "provider": self.provider_name,
        }
