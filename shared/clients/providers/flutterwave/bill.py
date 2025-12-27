"""Flutterwave Bills client for airtime and utility payments using v3 API."""

import uuid
from typing import Any

import httpx
import structlog

from shared.clients.abstractions.bill import BillPaymentProvider
from shared.config.settings import settings

logger = structlog.get_logger(__name__)

FLUTTERWAVE_BASE_URL = "https://api.flutterwave.com"


class FlutterwaveBillsClient(BillPaymentProvider):
    """Flutterwave bill payment provider for airtime, data, and utilities."""

    AIRTIME_BILLERS = {
        "MTN": {"biller_code": "BIL099", "item_code": "AT099"},
        "AIRTEL": {"biller_code": "BIL100", "item_code": "AT100"},
        "GLO": {"biller_code": "BIL101", "item_code": "AT101"},
        "9MOBILE": {"biller_code": "BIL102", "item_code": "AT102"},
        "ETISALAT": {"biller_code": "BIL102", "item_code": "AT102"},
    }

    def __init__(
        self,
        secret_key: str | None = None,
        use_sandbox: bool | None = None,
    ):
        """Initialize Flutterwave bills client with v3 secret key."""
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
        """Check if provider is properly configured."""
        return bool(self.secret_key)

    @property
    def supports_airtime(self) -> bool:
        """Flutterwave supports airtime purchases."""
        return True

    @property
    def supports_data(self) -> bool:
        """Flutterwave supports data purchases."""
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

    async def _request(
        self,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Unified HTTP request handler with error handling."""
        url = f"{self.base_url}{endpoint}"
        headers = self._get_headers()

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
                        "message": result.get("message"),
                    }
                error_data = result.get("error")
                if isinstance(error_data, dict):
                    error_msg = error_data.get("message", f"HTTP {response.status_code}")
                elif isinstance(error_data, str):
                    error_msg = error_data
                else:
                    error_msg = result.get("message") or f"HTTP {response.status_code}"

                return {"success": False, "error": error_msg}

        except httpx.ConnectError:
            return {"success": False, "error": "Failed to connect to service"}
        except Exception as e:
            logger.error("bills_api_error", error=str(e))
            return {"success": False, "error": f"Unexpected error: {str(e)}"}

    async def purchase_airtime(
        self,
        amount: float,
        recipient_phone: str,
        network: str,
        reference: str | None = None,
    ) -> dict[str, Any]:
        """Purchase airtime via Flutterwave Bills Payment API."""
        airtime_info = {"amount": amount, "recipient_phone": recipient_phone, "network": network}

        network_upper = network.upper().strip()
        biller_info = self.AIRTIME_BILLERS.get(network_upper)
        if not biller_info:
            return self._error_response(
                f"Unsupported network: {network}. Supported: MTN, Airtel, Glo, 9mobile",
                **airtime_info,
            )

        biller_code = biller_info["biller_code"]
        item_code = biller_info["item_code"]

        if not reference:
            reference = f"airtime-{uuid.uuid4().hex[:12]}"

        customer_phone = recipient_phone
        if customer_phone.startswith("+234"):
            customer_phone = "0" + customer_phone[4:]
        elif customer_phone.startswith("234"):
            customer_phone = "0" + customer_phone[3:]

        endpoint = f"/v3/billers/{biller_code}/items/{item_code}/payment"
        payload = {
            "country": "NG",
            "customer_id": customer_phone,
            "amount": amount,
            "reference": reference,
        }

        logger.info(
            "airtime_purchase_request", network=network, amount=amount, phone=customer_phone
        )
        result = await self._request("POST", endpoint, payload=payload)

        if result["success"]:
            data = result.get("data", {})
            tx_status = data.get("status", "successful").lower()

            return self._success_response(
                transaction_id=data.get("reference") or reference,
                status=tx_status,
                message=result.get("message", "Airtime purchase successful"),
                raw_response=data,
                **airtime_info,
            )

        return self._error_response(result.get("error", "Airtime purchase failed"), **airtime_info)

    async def fetch_bill_categories(self, category: str = "AIRTIME") -> dict[str, Any]:
        """Fetch available bill categories/billers from Flutterwave."""
        result = await self._request("GET", "/v3/bills/categories")

        if result["success"]:
            billers = result.get("data", [])
            if category:
                billers = [
                    b for b in billers if category.upper() in b.get("biller_name", "").upper()
                ]
            return self._success_response(billers=billers, count=len(billers))

        return self._error_response(result.get("error", "Failed to fetch categories"), billers=[])
