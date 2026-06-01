"""Mono implementation of DirectDebitProvider.

Uses Mono's Direct Debit API for pulling funds from user bank accounts.
"""

from shared.clients.abstractions.direct_debit import (
    BalanceResult,
    DebitResult,
    DebitStatus,
    DirectDebitProvider,
)
from shared.clients.providers.mono.client import MonoClient
from shared.clients.providers.mono.models import MonoApiError
from shared.money import MoneyAmount, naira_to_kobo, require_kobo_to_naira, require_naira
from shared.security.redaction import redact_sensitive_identifiers
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)


class MonoDirectDebitProvider(DirectDebitProvider):
    """
    Mono implementation of DirectDebitProvider.

    Uses Mono's v3 Direct Debit API for:
    - Balance checks (via account_id)
    - One-time debits (via mandate_id)
    - Debit status checks
    - Reversals/refunds
    """

    def __init__(self, mono_client: MonoClient | None = None):
        self._client = mono_client or MonoClient()

    @property
    def provider_name(self) -> str:
        return "mono"

    async def get_balance(self, account_id: str, real_time: bool = True) -> BalanceResult:
        """Get account balance via Mono."""
        try:
            balance_data = await self._client.get_balance(account_id, real_time=real_time)
            return BalanceResult(
                success=True,
                available_balance=balance_data.balance_naira,
                ledger_balance=balance_data.ledger_balance_naira,
                currency=balance_data.currency,
            )
        except Exception as e:
            logger.error("mono_get_balance_failed", account_id=account_id, error=str(e))
            return BalanceResult(
                success=False,
                available_balance=require_naira(0),
                error_message=str(e),
            )

    async def initiate_debit(
        self,
        mandate_id: str,
        amount: MoneyAmount,
        reference: str,
        narration: str = "Transfer",
        beneficiary_account: str | None = None,
        beneficiary_bank_code: str | None = None,
    ) -> DebitResult:
        """Initiate a one-time debit via Mono Direct Debit API."""
        has_beneficiary_account = bool(beneficiary_account)
        has_beneficiary_bank = bool(beneficiary_bank_code)
        if has_beneficiary_account != has_beneficiary_bank:
            return DebitResult(
                success=False,
                status=DebitStatus.FAILED,
                reference=reference,
                amount=require_naira(amount),
                error_message="Both beneficiary_account and beneficiary_bank_code must be provided together",
            )

        try:
            debit_amount_naira = require_naira(amount)
            amount_kobo = naira_to_kobo(debit_amount_naira)
            mode = "direct_beneficiary" if has_beneficiary_account else "pooling"

            response = await self._client.initiate_debit(
                mandate_id=mandate_id,
                amount=amount_kobo,
                reference=reference,
                narration=narration,
                beneficiary_account=beneficiary_account,
                beneficiary_bank_code=beneficiary_bank_code,
            )
            logger.info("mono_initiate_debit_mode", mode=mode, reference=reference)
            success, status, error_message = self._normalize_debit_outcome(response)

            return DebitResult(
                success=success,
                status=status,
                debit_id=response.get("id"),
                reference=response.get("reference") or reference,
                amount=debit_amount_naira,
                error_message=error_message,
                provider_response=redact_sensitive_identifiers(response),
            )
        except MonoApiError as e:
            logger.error("mono_initiate_debit_failed", mandate_id_hash=log_fingerprint(mandate_id), error=str(e))
            transient = self._is_transient_error(e)
            return DebitResult(
                success=transient,
                status=DebitStatus.PROCESSING if transient else DebitStatus.FAILED,
                reference=reference,
                amount=require_naira(amount),
                error_message=e.message,
                provider_response=redact_sensitive_identifiers(self._error_response(e)),
            )
        except Exception as e:
            logger.error("mono_initiate_debit_failed", mandate_id_hash=log_fingerprint(mandate_id), error=str(e))
            return DebitResult(
                success=True,
                status=DebitStatus.PROCESSING,
                reference=reference,
                amount=require_naira(amount),
                error_message=str(e),
                provider_response=redact_sensitive_identifiers(
                    {"http_status": 0, "message": str(e), "error_code": "CONNECTION_ERROR"}
                ),
            )

    async def get_debit_status(self, debit_id: str) -> DebitResult:
        """Get debit status from Mono."""
        try:
            response = await self._client.get_debit_status(debit_id)
            success, status, error_message = self._normalize_debit_outcome(response)

            return DebitResult(
                success=success,
                status=status,
                debit_id=debit_id,
                reference=response.get("reference"),
                amount=require_kobo_to_naira(response.get("amount", 0)),
                error_message=error_message,
                provider_response=redact_sensitive_identifiers(response),
            )
        except MonoApiError as e:
            logger.error("mono_get_debit_status_failed", debit_id=debit_id, error=str(e))
            transient = self._is_transient_error(e)
            return DebitResult(
                success=transient,
                status=DebitStatus.PROCESSING if transient else DebitStatus.FAILED,
                debit_id=debit_id,
                error_message=e.message,
                provider_response=redact_sensitive_identifiers(self._error_response(e)),
            )
        except Exception as e:
            logger.error("mono_get_debit_status_failed", debit_id=debit_id, error=str(e))
            return DebitResult(
                success=True,
                status=DebitStatus.PROCESSING,
                debit_id=debit_id,
                error_message=str(e),
                provider_response=redact_sensitive_identifiers(
                    {"http_status": 0, "message": str(e), "error_code": "CONNECTION_ERROR"}
                ),
            )

    async def get_debit_status_by_reference(self, reference: str) -> DebitResult:
        """Get debit status from Mono by original payment reference."""
        try:
            response = await self._client.verify_payment(reference)
            success, status, error_message = self._normalize_debit_outcome(response)

            return DebitResult(
                success=success,
                status=status,
                debit_id=response.get("id"),
                reference=response.get("reference") or reference,
                amount=require_kobo_to_naira(response.get("amount", 0)),
                error_message=error_message,
                provider_response=redact_sensitive_identifiers(response),
            )
        except MonoApiError as e:
            logger.error("mono_get_debit_status_by_reference_failed", reference=reference, error=str(e))
            transient = self._is_transient_error(e)
            return DebitResult(
                success=transient,
                status=DebitStatus.PROCESSING if transient else DebitStatus.FAILED,
                reference=reference,
                error_message=e.message,
                provider_response=redact_sensitive_identifiers(self._error_response(e)),
            )
        except Exception as e:
            logger.error("mono_get_debit_status_by_reference_failed", reference=reference, error=str(e))
            return DebitResult(
                success=True,
                status=DebitStatus.PROCESSING,
                reference=reference,
                error_message=str(e),
                provider_response=redact_sensitive_identifiers(
                    {"http_status": 0, "message": str(e), "error_code": "CONNECTION_ERROR"}
                ),
            )

    async def reverse_debit(self, debit_reference: str, reason: str = "Refund") -> DebitResult:
        """Refund a debit via Mono's payment refund API."""
        del reason
        try:
            response = await self._client.refund_payment(debit_reference)
            success, status, error_message = self._normalize_refund_outcome(response)
            return DebitResult(
                success=success,
                status=status,
                debit_id=response.get("id"),
                reference=response.get("reference") or debit_reference,
                error_message=error_message,
                provider_response=redact_sensitive_identifiers(response),
            )
        except MonoApiError as e:
            logger.error("mono_refund_failed", reference=debit_reference, error=str(e))
            transient = self._is_transient_error(e)
            return DebitResult(
                success=transient,
                status=DebitStatus.PROCESSING if transient else DebitStatus.FAILED,
                reference=debit_reference,
                error_message=e.message,
                provider_response=redact_sensitive_identifiers(self._error_response(e)),
            )
        except Exception as e:
            logger.error("mono_refund_failed", reference=debit_reference, error=str(e))
            return DebitResult(
                success=True,
                status=DebitStatus.PROCESSING,
                reference=debit_reference,
                error_message=str(e),
                provider_response=redact_sensitive_identifiers(
                    {"http_status": 0, "message": str(e), "error_code": "CONNECTION_ERROR"}
                ),
            )

    async def get_refund_status(self, debit_reference: str, refund_id: str | None = None) -> DebitResult:
        """Check refund status conservatively through Mono payment verification."""
        del refund_id
        try:
            response = await self._client.verify_payment(debit_reference)
            success, status, error_message = self._normalize_refund_verification_outcome(response)
            return DebitResult(
                success=success,
                status=status,
                debit_id=response.get("id"),
                reference=response.get("reference") or response.get("reference_number") or debit_reference,
                amount=require_kobo_to_naira(response["amount"]) if response.get("amount") is not None else None,
                error_message=error_message,
                provider_response=redact_sensitive_identifiers(response),
            )
        except MonoApiError as e:
            logger.error("mono_get_refund_status_failed", reference=debit_reference, error=str(e))
            transient = self._is_transient_error(e)
            return DebitResult(
                success=transient,
                status=DebitStatus.PROCESSING if transient else DebitStatus.FAILED,
                reference=debit_reference,
                error_message=e.message,
                provider_response=redact_sensitive_identifiers(self._error_response(e)),
            )
        except Exception as e:
            logger.error("mono_get_refund_status_failed", reference=debit_reference, error=str(e))
            return DebitResult(
                success=True,
                status=DebitStatus.PROCESSING,
                reference=debit_reference,
                error_message=str(e),
                provider_response=redact_sensitive_identifiers(
                    {"http_status": 0, "message": str(e), "error_code": "CONNECTION_ERROR"}
                ),
            )

    async def cancel_mandate(self, mandate_id: str) -> bool:
        """Cancel a mandate via Mono."""
        try:
            return await self._client.cancel_mandate(mandate_id)
        except Exception as e:
            logger.error("mono_cancel_mandate_failed", mandate_id_hash=log_fingerprint(mandate_id), error=str(e))
            return False

    def _map_status(self, mono_status: str) -> DebitStatus:
        """Map Mono status to our DebitStatus enum."""
        status_map = {
            "pending": DebitStatus.PENDING,
            "processing": DebitStatus.PROCESSING,
            "successful": DebitStatus.SUCCESSFUL,
            "success": DebitStatus.SUCCESSFUL,
            "failed": DebitStatus.FAILED,
            "reversed": DebitStatus.REVERSED,
        }
        return status_map.get(mono_status.lower(), DebitStatus.PENDING)

    @staticmethod
    def _response_code(response: dict | None) -> str | None:
        """Extract Mono response code when present."""
        if not isinstance(response, dict):
            return None
        code = response.get("response_code")
        if code is None:
            code = response.get("responseCode")
        return None if code is None else str(code)

    @staticmethod
    def _response_message(response: dict | None) -> str | None:
        """Extract the best available provider error message."""
        if not isinstance(response, dict):
            return None
        for key in ("message", "response_message", "description", "reason"):
            value = response.get(key)
            if value is not None:
                return str(value)
        return None

    def _normalize_debit_outcome(self, response: dict | None) -> tuple[bool, DebitStatus, str | None]:
        """Normalize Mono debit status using response_code when available."""
        mono_status = str((response or {}).get("status", "pending"))
        mapped_status = self._map_status(mono_status)
        response_code = self._response_code(response)

        if response_code is None:
            success = mapped_status != DebitStatus.FAILED
            error_message = self._response_message(response) if mapped_status == DebitStatus.FAILED else None
            return success, mapped_status, error_message

        if mapped_status == DebitStatus.SUCCESSFUL:
            if response_code == "00":
                return True, DebitStatus.SUCCESSFUL, None
            return False, DebitStatus.FAILED, self._response_message(response)

        if mapped_status == DebitStatus.FAILED:
            return False, DebitStatus.FAILED, self._response_message(response)

        return True, mapped_status, None

    def _normalize_refund_outcome(self, response: dict | None) -> tuple[bool, DebitStatus, str | None]:
        """Normalize Mono refund status into the direct-debit status model."""
        refund_status = str((response or {}).get("status", "pending")).lower()
        response_code = self._response_code(response)

        if refund_status in {"successful", "success", "reversed", "refunded"}:
            if response_code in (None, "00"):
                return True, DebitStatus.REVERSED, None
            return False, DebitStatus.FAILED, self._response_message(response)

        if refund_status in {"failed", "failure"}:
            return False, DebitStatus.FAILED, self._response_message(response)

        return True, DebitStatus.PENDING, None

    def _normalize_refund_verification_outcome(self, response: dict | None) -> tuple[bool, DebitStatus, str | None]:
        """Normalize verified payment state for refund reconciliation.

        A verified original payment still being successful is not proof that the
        refund completed, so it remains pending.
        """
        status = str((response or {}).get("status", "pending")).lower()
        response_code = self._response_code(response)

        if status in {"reversed", "refunded"}:
            if response_code in (None, "00"):
                return True, DebitStatus.REVERSED, None
            return False, DebitStatus.FAILED, self._response_message(response)

        if status in {"failed", "failure"}:
            return False, DebitStatus.FAILED, self._response_message(response)

        return True, DebitStatus.PENDING, None

    @staticmethod
    def _is_transient_error(error: MonoApiError) -> bool:
        """Return whether a Mono API error should be reconciled/retried."""
        return error.http_status == 0 or error.is_rate_limited or error.is_server_error

    @staticmethod
    def _error_response(error: MonoApiError) -> dict:
        """Convert MonoApiError into provider_response metadata."""
        return {
            "http_status": error.http_status,
            "message": error.message,
            "error_code": error.error_code,
        }
