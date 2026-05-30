"""Flutterwave API client for payment services using v3 API."""

import uuid
from typing import Any
from urllib.parse import urlencode

import structlog

from shared.clients.abstractions.payment import PayoutProvider
from shared.clients.providers.flutterwave.client import FlutterwaveClient
from shared.money import MoneyAmount, naira_to_json, naira_to_provider_value, require_naira
from shared.utils.logging import log_fingerprint

logger = structlog.get_logger(__name__)


TERMINAL_SUCCESS_STATUSES = frozenset({"success", "successful", "completed"})
TERMINAL_FAILURE_STATUSES = frozenset({"failed", "failure", "cancelled", "canceled", "reversed"})
NON_TERMINAL_STATUSES = frozenset({"new", "pending", "processing", "queued", "in_progress"})


class FlutterwavePaymentProvider(PayoutProvider):
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

    @staticmethod
    def _normalize_status(status: Any) -> str:
        raw_status = str(status or "").strip().lower()
        if raw_status in TERMINAL_SUCCESS_STATUSES:
            return "successful"
        if raw_status in TERMINAL_FAILURE_STATUSES:
            return "failed"
        if raw_status in NON_TERMINAL_STATUSES:
            return "pending"
        return "pending"

    @staticmethod
    def _reference() -> str:
        return f"flw-trf-{uuid.uuid4().hex}"

    @staticmethod
    def _transfer_id(data: dict[str, Any]) -> str | None:
        for key in ("id", "transfer_id", "transaction_id"):
            value = data.get(key)
            if value is not None:
                return str(value)
        return None

    @staticmethod
    def _transfer_reference(data: dict[str, Any], fallback: str | None = None) -> str | None:
        for key in ("reference", "tx_ref"):
            value = data.get(key)
            if value:
                return str(value)
        return fallback

    @staticmethod
    def _transfer_error(data: dict[str, Any], fallback: str | None = None) -> str | None:
        for key in ("complete_message", "processor_response", "message", "narration"):
            value = data.get(key)
            if value:
                return str(value)
        return fallback

    def _response_from_transfer_data(
        self,
        data: dict[str, Any],
        *,
        reference: str | None = None,
        amount: MoneyAmount | None = None,
        recipient_account_number: str | None = None,
        recipient_bank_code: str | None = None,
        currency: str = "NGN",
        fallback_status: str = "pending",
    ) -> dict[str, Any]:
        raw_status = data.get("status") or fallback_status
        status = self._normalize_status(raw_status)
        success = status == "successful"
        amount_naira = naira_to_json(data.get("amount")) or naira_to_json(amount)
        response = {
            "success": success,
            "transaction_id": self._transfer_id(data),
            "reference": self._transfer_reference(data, reference),
            "status": status,
            "provider_status": str(raw_status),
            "amount": amount_naira,
            "amount_naira": amount_naira,
            "recipient_account_number": recipient_account_number or data.get("account_number"),
            "recipient_bank_code": recipient_bank_code or data.get("account_bank") or data.get("bank_code"),
            "currency": data.get("currency") or currency,
            "provider": self.provider_name,
            "raw_response": data,
        }
        if status == "failed":
            response["error"] = self._transfer_error(data, "Flutterwave transfer failed")
        return response

    @staticmethod
    def _is_duplicate_reference_error(result: dict[str, Any]) -> bool:
        error = str(result.get("error") or result.get("message") or "").lower()
        return "duplicate" in error and "reference" in error

    async def get_transfer_by_reference(self, reference: str) -> dict[str, Any]:
        """Fetch a transfer by merchant reference for idempotency recovery."""
        params = urlencode({"reference": reference, "page_size": "1"})
        result = await self._client.request("GET", f"/v3/transfers?{params}", max_retries=1)
        if not result.get("success"):
            return self._error_response(
                str(result.get("error") or "Transfer lookup failed"),
                status="pending",
                reference=reference,
                status_code=result.get("status_code"),
            )

        data = result.get("data") or {}
        transfers: list[dict[str, Any]]
        if isinstance(data, list):
            transfers = [item for item in data if isinstance(item, dict)]
        elif isinstance(data, dict):
            raw_transfers = data.get("data") or data.get("transfers") or data.get("items") or []
            transfers = [item for item in raw_transfers if isinstance(item, dict)]
            if not transfers and (data.get("reference") or data.get("id")):
                transfers = [data]
        else:
            transfers = []

        exact_matches = [item for item in transfers if self._transfer_reference(item) == reference]
        transfer = exact_matches[0] if exact_matches else (transfers[0] if transfers else None)
        if not transfer:
            return self._error_response("Transfer not found for reference", status="pending", reference=reference)

        return self._response_from_transfer_data(transfer, reference=reference)

    async def initiate_transfer(
        self,
        amount: MoneyAmount,
        recipient_account_number: str,
        recipient_bank_code: str,
        sender_account_number: str | None = None,
        narration: str | None = None,
        currency: str = "NGN",
        reference: str | None = None,
    ) -> dict[str, Any]:
        """Initiate a bank transfer via Flutterwave."""
        del sender_account_number
        reference = str(reference or self._reference()).strip()
        transfer_amount = require_naira(amount)
        payload = {
            "account_bank": recipient_bank_code,
            "account_number": recipient_account_number,
            "amount": naira_to_provider_value(transfer_amount),
            "currency": currency,
            "reference": reference,
        }
        if narration:
            payload["narration"] = narration

        logger.info(
            "flutterwave_transfer_initiate_request",
            amount=str(transfer_amount),
            currency=currency,
            recipient_account_hash=log_fingerprint(recipient_account_number),
            recipient_bank_code=recipient_bank_code,
            reference_hash=log_fingerprint(reference),
        )

        result = await self._client.request("POST", "/v3/transfers", payload=payload, max_retries=1)
        if result.get("success"):
            data = result.get("data") or {}
            if not isinstance(data, dict):
                data = {}
            return self._response_from_transfer_data(
                data,
                reference=reference,
                amount=transfer_amount,
                recipient_account_number=recipient_account_number,
                recipient_bank_code=recipient_bank_code,
                currency=currency,
            )

        if self._is_duplicate_reference_error(result):
            logger.warning("flutterwave_transfer_duplicate_reference", reference_hash=log_fingerprint(reference))
            lookup = await self.get_transfer_by_reference(reference)
            if lookup.get("transaction_id"):
                return lookup
            return {
                **lookup,
                "success": False,
                "status": "pending",
                "error": lookup.get("error") or "Duplicate transfer reference; status unknown",
                "reference": reference,
            }

        status_code = int(result.get("status_code") or 0)
        transient_failure = bool(result.get("circuit_open")) or status_code == 0 or status_code >= 500
        status = "pending" if transient_failure else "failed"
        return self._error_response(
            str(result.get("error") or "Flutterwave transfer initiation failed"),
            status=status,
            reference=reference,
            amount=transfer_amount,
            recipient_account_number=recipient_account_number,
            recipient_bank_code=recipient_bank_code,
            currency=currency,
            status_code=status_code,
        )

    async def get_transfer_status(self, transaction_id: str) -> dict[str, Any]:
        """Get transfer status from Flutterwave."""
        transfer_id = str(transaction_id or "").strip()
        if not transfer_id:
            return self._error_response("Transfer transaction id is required", status="failed")

        result = await self._client.request("GET", f"/v3/transfers/{transfer_id}", max_retries=1)
        if not result.get("success"):
            status_code = int(result.get("status_code") or 0)
            transient_failure = bool(result.get("circuit_open")) or status_code == 0 or status_code >= 500
            return self._error_response(
                str(result.get("error") or "Transfer status lookup failed"),
                transaction_id=transfer_id,
                status="pending" if transient_failure else "failed",
                status_code=status_code,
            )

        data = result.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        return self._response_from_transfer_data(data, fallback_status="pending")
