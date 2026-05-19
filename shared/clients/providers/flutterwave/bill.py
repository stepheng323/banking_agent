"""Flutterwave Bills client for airtime and utility payments using v3 API."""

import uuid
from typing import Any

import structlog

from shared.clients.abstractions.bill import BillPaymentProvider
from shared.clients.providers.flutterwave.client import FlutterwaveClient
from shared.utils.logging import log_fingerprint

logger = structlog.get_logger(__name__)


class FlutterwaveBillsClient(BillPaymentProvider):
    """Flutterwave bill payment provider for airtime, data, and utilities."""

    AIRTIME_BILLERS = {
        "MTN": {"biller_code": "BIL099", "item_code": "AT099"},
        "AIRTEL": {"biller_code": "BIL100", "item_code": "AT100"},
        "GLO": {"biller_code": "BIL101", "item_code": "AT101"},
        "9MOBILE": {"biller_code": "BIL102", "item_code": "AT102"},
        "ETISALAT": {"biller_code": "BIL102", "item_code": "AT102"},
    }

    DATA_BILLERS = {
        "MTN": {"biller_code": "BIL108"},
        "AIRTEL": {"biller_code": "BIL109"},
        "GLO": {"biller_code": "BIL110"},
        "9MOBILE": {"biller_code": "BIL111"},
        "ETISALAT": {"biller_code": "BIL111"},
    }

    def __init__(
        self,
        client: FlutterwaveClient | None = None,
    ):
        """Initialize Flutterwave bills client with injected client."""
        self._client = client or FlutterwaveClient()
        self.base_url = self._client.base_url

    @property
    def provider_name(self) -> str:
        """Return the name of the provider."""
        return "flutterwave"

    @property
    def is_available(self) -> bool:
        """Check if provider is properly configured."""
        return self._client.is_configured

    @property
    def supports_airtime(self) -> bool:
        """Flutterwave supports airtime purchases."""
        return True

    @property
    def supports_data(self) -> bool:
        """Flutterwave supports data purchases."""
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

    def _normalize_phone(self, phone: str) -> str:
        """Normalize phone number to local format (0XXX...)."""
        if phone.startswith("+234"):
            return "0" + phone[4:]
        elif phone.startswith("234"):
            return "0" + phone[3:]
        return phone

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

        customer_phone = self._normalize_phone(recipient_phone)

        endpoint = f"/v3/billers/{biller_code}/items/{item_code}/payment"
        payload = {
            "country": "NG",
            "customer_id": customer_phone,
            "amount": amount,
            "reference": reference,
        }

        logger.info(
            "airtime_purchase_request",
            network=network,
            amount=amount,
            phone_hash=log_fingerprint(customer_phone),
        )
        result = await self._client.request("POST", endpoint, payload=payload)

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
        result = await self._client.request("GET", "/v3/bills/categories")

        if result["success"]:
            billers = result.get("data", [])
            if category:
                billers = [b for b in billers if category.upper() in b.get("biller_name", "").upper()]
            return self._success_response(billers=billers, count=len(billers))

        return self._error_response(result.get("error", "Failed to fetch categories"), billers=[])

    async def get_data_plans(self, network: str) -> dict[str, Any]:
        """Get available data plans for a network from Flutterwave."""
        network_upper = network.upper().strip()
        biller_info = self.DATA_BILLERS.get(network_upper)

        if not biller_info:
            return self._error_response(
                f"Unsupported network: {network}. Supported: MTN, Airtel, Glo, 9mobile",
                plans=[],
            )

        biller_code = biller_info["biller_code"]
        endpoint = f"/v3/billers/{biller_code}/items"

        result = await self._client.request("GET", endpoint)

        if result["success"]:
            items = result.get("data", [])
            plans = []
            for item in items:
                plans.append(
                    {
                        "item_code": item.get("item_code"),
                        "name": item.get("name"),
                        "amount": item.get("amount"),
                        "biller_code": biller_code,
                    }
                )
            return self._success_response(plans=plans, network=network_upper, count=len(plans))

        return self._error_response(result.get("error", "Failed to fetch data plans"), plans=[])

    async def purchase_data(
        self,
        plan_code: str,
        recipient_phone: str,
        network: str,
        reference: str | None = None,
    ) -> dict[str, Any]:
        """Purchase a data plan via Flutterwave Bills Payment API."""
        data_info = {"plan_code": plan_code, "recipient_phone": recipient_phone, "network": network}

        network_upper = network.upper().strip()
        biller_info = self.DATA_BILLERS.get(network_upper)

        if not biller_info:
            return self._error_response(
                f"Unsupported network: {network}. Supported: MTN, Airtel, Glo, 9mobile",
                **data_info,
            )

        biller_code = biller_info["biller_code"]

        if not reference:
            reference = f"data-{uuid.uuid4().hex[:12]}"

        customer_phone = self._normalize_phone(recipient_phone)

        endpoint = f"/v3/billers/{biller_code}/items/{plan_code}/payment"
        payload = {
            "country": "NG",
            "customer_id": customer_phone,
            "reference": reference,
        }

        logger.info(
            "data_purchase_request",
            network=network,
            plan_code=plan_code,
            phone=customer_phone,
        )
        result = await self._client.request("POST", endpoint, payload=payload)

        if result["success"]:
            data = result.get("data", {})
            tx_status = data.get("status", "successful").lower()

            return self._success_response(
                transaction_id=data.get("reference") or reference,
                status=tx_status,
                message=result.get("message", "Data purchase successful"),
                raw_response=data,
                **data_info,
            )

        return self._error_response(result.get("error", "Data purchase failed"), **data_info)
