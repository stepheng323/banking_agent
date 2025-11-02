"""Flutterwave API client for account verification and resolution."""
import asyncio
from typing import Any, Dict, Optional

import httpx
from shared.config.settings import settings


FLUTTERWAVE_BASE_URL = "https://api.flutterwave.com"
FLUTTERWAVE_SANDBOX_URL = "https://developersandbox-api.flutterwave.com"


class FlutterwaveClient:
    """Client for interacting with Flutterwave API."""

    def __init__(self, secret_key: Optional[str] = None, use_sandbox: Optional[bool] = None):
        """Initialize Flutterwave client with secret key."""
        self.secret_key = secret_key or getattr(
            settings, "flutterwave_secret_key", None)
        if not self.secret_key:
            raise ValueError(
                "Flutterwave secret key not configured. Set FLUTTERWAVE_SECRET_KEY environment variable."
            )
        self.use_sandbox = use_sandbox if use_sandbox is not None else getattr(
            settings, "flutterwave_use_sandbox", False)
        self.base_url = FLUTTERWAVE_SANDBOX_URL if self.use_sandbox else FLUTTERWAVE_BASE_URL

    def _get_headers(self) -> Dict[str, str]:
        """Get request headers with authentication."""
        return {
            "Authorization": f"Bearer {self.secret_key}",
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
        headers = self._get_headers()

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
                    }

            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code == 401:
                    print("❌ Flutterwave API Authentication Failed (401)")
                    print("   Check your FLUTTERWAVE_SECRET_KEY")
                    return {
                        "success": False,
                        "error": "Authentication failed. Please check API configuration.",
                        "account_number": account_number,
                        "bank_code": bank_code,
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
                        }
                    except Exception:
                        return {
                            "success": False,
                            "error": "Invalid account number or bank code",
                            "account_number": account_number,
                            "bank_code": bank_code,
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
                    }

        return {
            "success": False,
            "error": f"Failed after {max_retries} attempts: {str(last_error)}",
            "account_number": account_number,
            "bank_code": bank_code,
        }
