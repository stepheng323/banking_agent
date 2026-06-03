"""Flutterwave Bills client for airtime and utility payments using v3 API."""

import uuid
from typing import Any

import structlog

from shared.clients.abstractions.bill import BillPaymentProvider
from shared.clients.providers.flutterwave.client import FlutterwaveClient
from shared.money import MoneyAmount, naira_to_json, naira_to_provider_value, require_naira
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
        "MTN": {"biller_code": "BIL104"},
        "GLO": {"biller_code": "BIL105"},
        "AIRTEL": {"biller_code": "BIL106"},
        "9MOBILE": {"biller_code": "BIL107"},
        "ETISALAT": {"biller_code": "BIL107"},
    }

    def __init__(
        self,
        client: FlutterwaveClient | None = None,
    ):
        """Initialize Flutterwave bills client with injected client."""
        self._client = client or FlutterwaveClient()
        self.base_url = self._client.base_url
        self._data_biller_cache: dict[str, str] = {}

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
        amount: MoneyAmount,
        recipient_phone: str,
        network: str,
        reference: str | None = None,
    ) -> dict[str, Any]:
        """Purchase airtime via Flutterwave Bills Payment API."""
        airtime_amount = require_naira(amount)
        airtime_amount_naira = naira_to_json(airtime_amount)
        airtime_info = {
            "amount": airtime_amount_naira,
            "amount_naira": airtime_amount_naira,
            "recipient_phone": recipient_phone,
            "network": network,
        }

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
            "amount": naira_to_provider_value(airtime_amount),
            "reference": reference,
        }

        logger.info(
            "airtime_purchase_request",
            network=network,
            amount=str(airtime_amount),
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

    @staticmethod
    def _canonical_network(network: str) -> str:
        normalized = network.upper().strip()
        if normalized == "ETISALAT":
            return "9MOBILE"
        return normalized

    @staticmethod
    def _network_from_biller(item: dict[str, Any]) -> str | None:
        text = " ".join(
            str(item.get(key) or "") for key in ("name", "description", "short_name", "biller_name", "group_name")
        ).upper()
        if "MTN" in text:
            return "MTN"
        if "AIRTEL" in text:
            return "AIRTEL"
        if "GLO" in text:
            return "GLO"
        if "9MOBILE" in text or "ETISALAT" in text:
            return "9MOBILE"
        return None

    async def _discover_data_billers(self) -> dict[str, str]:
        """Discover Nigerian mobile-data billers from Flutterwave."""
        result = await self._client.request("GET", "/v3/bills/MOBILEDATA/billers?country=NG")
        if not result.get("success"):
            logger.warning("flutterwave_data_biller_discovery_failed", error=result.get("error"))
            return {}

        discovered: dict[str, str] = {}
        for item in result.get("data") or []:
            if not isinstance(item, dict):
                continue
            network = self._network_from_biller(item)
            biller_code = str(item.get("biller_code") or "").strip()
            if network and biller_code:
                discovered[network] = biller_code
        if discovered:
            self._data_biller_cache.update(discovered)
        return discovered

    async def _resolve_data_biller_code(self, network: str) -> str | None:
        network_upper = self._canonical_network(network)
        if cached := self._data_biller_cache.get(network_upper):
            return cached

        discovered = await self._discover_data_billers()
        if discovered.get(network_upper):
            return discovered[network_upper]

        fallback = self.DATA_BILLERS.get(network_upper)
        return str(fallback.get("biller_code")) if fallback else None

    async def get_data_plans(self, network: str) -> dict[str, Any]:
        """Get available data plans for a network from Flutterwave."""
        network_upper = self._canonical_network(network)
        biller_code = await self._resolve_data_biller_code(network_upper)

        if not biller_code:
            return self._error_response(
                f"Unsupported network: {network}. Supported: MTN, Airtel, Glo, 9mobile",
                plans=[],
            )

        endpoint = f"/v3/billers/{biller_code}/items"

        result = await self._client.request("GET", endpoint)

        if result["success"]:
            items = result.get("data", [])
            plans = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                plans.append(
                    {
                        "item_code": item.get("item_code"),
                        "name": item.get("biller_name") or item.get("short_name") or item.get("name"),
                        "amount": item.get("amount"),
                        "biller_code": item.get("biller_code") or biller_code,
                        "biller_name": item.get("biller_name"),
                        "short_name": item.get("short_name"),
                        "validity_period": item.get("validity_period"),
                        "category_name": item.get("category_name"),
                        "group_name": item.get("group_name"),
                        "is_data": item.get("is_data"),
                        "raw_item": {
                            key: value
                            for key, value in item.items()
                            if isinstance(value, str | int | float | bool)
                            and key
                            in {
                                "id",
                                "item_code",
                                "biller_code",
                                "biller_name",
                                "short_name",
                                "amount",
                                "validity_period",
                                "category_name",
                                "group_name",
                                "country",
                                "is_data",
                            }
                        },
                    }
                )
            return self._success_response(plans=plans, network=network_upper, count=len(plans))

        return self._error_response(result.get("error", "Failed to fetch data plans"), plans=[])

    async def purchase_data(
        self,
        plan_code: str,
        recipient_phone: str,
        network: str,
        amount: MoneyAmount | None = None,
        reference: str | None = None,
    ) -> dict[str, Any]:
        """Purchase a data plan via Flutterwave Bills Payment API."""
        data_amount = require_naira(amount) if amount is not None else None
        data_amount_naira = naira_to_json(data_amount)
        data_info = {
            "plan_code": plan_code,
            "recipient_phone": recipient_phone,
            "network": network,
            "amount": data_amount_naira,
            "amount_naira": data_amount_naira,
        }

        network_upper = self._canonical_network(network)
        biller_code = await self._resolve_data_biller_code(network_upper)

        if not biller_code:
            return self._error_response(
                f"Unsupported network: {network}. Supported: MTN, Airtel, Glo, 9mobile",
                **data_info,
            )

        if not reference:
            reference = f"data-{uuid.uuid4().hex[:12]}"

        customer_phone = self._normalize_phone(recipient_phone)

        endpoint = f"/v3/billers/{biller_code}/items/{plan_code}/payment"
        payload: dict[str, int | str] = {
            "country": "NG",
            "customer_id": customer_phone,
            "reference": reference,
        }
        if data_amount is not None:
            payload["amount"] = naira_to_provider_value(data_amount) or "0.00"

        logger.info(
            "data_purchase_request",
            network=network,
            plan_code=plan_code,
            phone_hash=log_fingerprint(customer_phone),
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

    async def get_bill_status(self, reference: str) -> dict[str, Any]:
        """Get Flutterwave bill payment status by payment reference."""
        normalized_reference = str(reference or "").strip()
        if not normalized_reference:
            return self._error_response("Bill reference is required")

        result = await self._client.request("GET", f"/v3/bills/{normalized_reference}?verbose=1")
        if result.get("success"):
            data = result.get("data") or {}
            status = str(data.get("status") or data.get("transaction_status") or "").strip().lower()
            return self._success_response(
                transaction_id=data.get("reference") or normalized_reference,
                reference=data.get("reference") or normalized_reference,
                status=status or "successful",
                raw_response=data,
            )
        return self._error_response(result.get("error", "Failed to fetch bill status"), reference=normalized_reference)
