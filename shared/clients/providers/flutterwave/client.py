"""Generic Flutterwave API Client."""

import asyncio
from typing import Any

import httpx
import structlog

from shared.config.settings import settings
from shared.resilience.circuit_breaker import flutterwave_circuit

logger = structlog.get_logger(__name__)


class FlutterwaveClient:
    """
    Generic HTTP client for Flutterwave API v3.

    Handles:
    - Authentication (Secret Key)
    - HTTP Requests & Retries
    - Error Response Parsing
    - Circuit Breaker Integration
    """

    BASE_URL = "https://api.flutterwave.com"

    def __init__(
        self,
        secret_key: str | None = None,
        use_sandbox: bool | None = None,
    ):
        """
        Initialize client.

        Args:
            secret_key: v3 API secret key
            use_sandbox: Whether to use sandbox environment
        """
        self.secret_key = secret_key or getattr(settings, "flutterwave_secret_key", None)

        # We don't raise error here for flexibility, but providers should check availability
        self.use_sandbox = (
            use_sandbox if use_sandbox is not None else getattr(settings, "flutterwave_use_sandbox", False)
        )
        self.base_url = self.BASE_URL

    @property
    def is_configured(self) -> bool:
        """Check if API key is present."""
        return bool(self.secret_key)

    def _get_headers(self) -> dict[str, str]:
        """Get request headers."""
        return {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
            "accept": "application/json",
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

    async def request(
        self,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 30.0,
        max_retries: int = 1,
    ) -> dict[str, Any]:
        """
        Execute HTTP request.

        Returns:
            Dict with success, data, error, status_code
        """
        if not self.secret_key:
            return {
                "success": False,
                "error": "Flutterwave secret key not configured",
                "status_code": 0,
            }

        url = f"{self.base_url}{endpoint}"
        headers = self._get_headers()
        last_error = None

        if flutterwave_circuit.is_open:
            return {
                "success": False,
                "error": "Service temporarily unavailable. Please try again in a minute.",
                "status_code": 503,
                "circuit_open": True,
            }

        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    if method.upper() == "GET":
                        response = await client.get(url, headers=headers)
                    else:
                        response = await client.post(url, headers=headers, json=payload)

                    result = response.json()

                    # Success Case
                    if response.status_code == 200 and result.get("status") == "success":
                        await flutterwave_circuit._on_success()
                        return {
                            "success": True,
                            "data": result.get("data", result),
                            "status_code": response.status_code,
                            "message": result.get("message"),
                        }

                    # Auth Failure
                    if response.status_code == 401:
                        logger.error("api_auth_failed", endpoint=endpoint)
                        return {
                            "success": False,
                            "error": "Authentication failed. Check your secret key.",
                            "status_code": 401,
                        }

                    # Bad Request (No Retry)
                    if response.status_code == 400:
                        error_msg = self._extract_error_message(response)
                        return {
                            "success": False,
                            "error": error_msg,
                            "status_code": 400,
                        }

                    # Generic Retryable Error
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
                await flutterwave_circuit._on_failure(e)
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
