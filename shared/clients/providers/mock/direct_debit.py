"""Mock implementation of DirectDebitProvider for development and testing."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from shared.clients.abstractions.direct_debit import (
    BalanceResult,
    DebitResult,
    DebitStatus,
    DirectDebitProvider,
)
from shared.money import MoneyAmount, require_naira, to_naira
from shared.security.redaction import redact_sensitive_identifiers
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)

DEFAULT_MOCK_BALANCE = Decimal("30000.00")


class MockDirectDebitProvider(DirectDebitProvider):
    """
    Mock implementation of DirectDebitProvider for development.

    Simulates debit operations with configurable balances and behaviors.
    Default balance is 30k to easily test multi-account funding.
    """

    def __init__(self):
        self._balances: dict[str, MoneyAmount] = {
            "mock_account_1": Decimal("150000.00"),
            "mock_account_2": Decimal("75000.00"),
            "mock_account_3": Decimal("25000.00"),
        }
        self._debits: dict[str, dict] = {}

    @staticmethod
    def _response_code(payload: dict | None) -> str | None:
        if not isinstance(payload, dict):
            return None
        code = payload.get("response_code")
        if code is None:
            code = payload.get("responseCode")
        return None if code is None else str(code)

    @staticmethod
    def _response_message(payload: dict | None) -> str | None:
        if not isinstance(payload, dict):
            return None
        for key in ("message", "response_message", "description", "reason"):
            value = payload.get(key)
            if value is not None:
                return str(value)
        return None

    @classmethod
    def _build_result(cls, payload: dict, *, amount_naira: MoneyAmount | None = None) -> DebitResult:
        code = cls._response_code(payload)
        status = payload.get("status", DebitStatus.PENDING.value)
        amount = amount_naira if amount_naira is not None else require_naira(payload.get("amount", 0))
        if status == DebitStatus.SUCCESSFUL.value:
            success = code in (None, "00")
            normalized_status = DebitStatus.SUCCESSFUL if success else DebitStatus.FAILED
        elif status == DebitStatus.FAILED.value:
            success = False
            normalized_status = DebitStatus.FAILED
        elif status == DebitStatus.PROCESSING.value:
            success = True
            normalized_status = DebitStatus.PROCESSING
        else:
            success = True
            normalized_status = DebitStatus.PENDING
        error_message = cls._response_message(payload) if not success else None
        return DebitResult(
            success=success,
            status=normalized_status,
            debit_id=str(payload.get("id") or payload.get("debit_id") or ""),
            reference=str(payload.get("reference") or ""),
            amount=amount,
            error_message=error_message,
            provider_response=redact_sensitive_identifiers(dict(payload)),
        )

    @property
    def provider_name(self) -> str:
        return "mock"

    def set_balance(self, account_id: str, balance: MoneyAmount | int | str) -> None:
        """Set balance for testing."""
        parsed_balance = require_naira(balance)
        self._balances[account_id] = parsed_balance
        logger.info("mock_balance_set", account_id=account_id, balance=str(parsed_balance))

    async def get_balance(self, account_id: str, real_time: bool = True) -> BalanceResult:
        """Get simulated balance. Returns 30k default for unknown accounts."""
        del real_time
        balance = self._balances.get(account_id, DEFAULT_MOCK_BALANCE)
        logger.info("mock_get_balance", account_id=account_id, balance=str(balance))
        return BalanceResult(
            success=True,
            available_balance=balance,
            ledger_balance=balance,
            currency="NGN",
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
        """Simulate debit initiation."""
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

        debit_id = f"mock_debit_{uuid.uuid4().hex[:8]}"
        now = datetime.now(UTC).isoformat()
        mode = "direct-to-beneficiary" if has_beneficiary_account else "pooling"
        payload = {
            "id": debit_id,
            "mandate_id": mandate_id,
            "amount": require_naira(amount),
            "reference": reference,
            "status": DebitStatus.SUCCESSFUL.value,
            "response_code": "00",
            "narration": narration,
            "debit_type": mode,
            "beneficiary": (
                {"account_number": beneficiary_account, "bank_code": beneficiary_bank_code}
                if has_beneficiary_account
                else None
            ),
            "created_at": now,
            "updated_at": now,
        }
        self._debits[reference] = payload

        logger.info(
            "mock_initiate_debit",
            debit_id=debit_id,
            amount=str(require_naira(amount)),
            reference=reference,
            mode="direct_beneficiary" if has_beneficiary_account else "pooling",
            default_status=DebitStatus.SUCCESSFUL.value,
        )
        return self._build_result(payload, amount_naira=amount)

    async def get_debit_status(self, debit_id: str) -> DebitResult:
        """Get simulated debit status with Mono-like lifecycle semantics."""
        for _ref, debit in self._debits.items():
            if debit.get("id") == debit_id or debit.get("debit_id") == debit_id:
                current_status = str(debit.get("status", DebitStatus.PENDING.value))
                next_status = {
                    DebitStatus.PENDING.value: DebitStatus.PROCESSING.value,
                    DebitStatus.PROCESSING.value: DebitStatus.SUCCESSFUL.value,
                }.get(current_status, current_status)
                debit["status"] = next_status
                debit["updated_at"] = datetime.now(UTC).isoformat()
                if next_status == DebitStatus.SUCCESSFUL.value:
                    debit["response_code"] = "00"
                    debit.pop("message", None)
                elif next_status == DebitStatus.FAILED.value and "response_code" not in debit:
                    debit["response_code"] = "51"
                    debit["message"] = "Debit failed"
                return self._build_result(debit, amount_naira=to_naira(debit.get("amount")) or Decimal("0.00"))

        return DebitResult(
            success=False,
            status=DebitStatus.FAILED,
            debit_id=debit_id,
            error_message="Mock debit not found",
            provider_response={"id": debit_id, "status": DebitStatus.FAILED.value, "response_code": "404"},
        )

    async def get_debit_status_by_reference(self, reference: str) -> DebitResult:
        """Get simulated debit status by merchant reference."""
        debit = self._debits.get(reference)
        if debit is None:
            return DebitResult(
                success=False,
                status=DebitStatus.FAILED,
                reference=reference,
                error_message="Mock debit not found",
                provider_response={"reference": reference, "status": DebitStatus.FAILED.value, "response_code": "404"},
            )
        return await self.get_debit_status(str(debit.get("id") or debit.get("debit_id") or reference))

    async def reverse_debit(self, debit_reference: str, reason: str = "Refund") -> DebitResult:
        """Simulate debit reversal."""
        logger.info("mock_reverse_debit", reference=debit_reference, reason=reason)
        debit = self._debits.get(debit_reference)
        debit_id = debit.get("id") if debit else debit_reference
        if debit is not None:
            debit["status"] = DebitStatus.REVERSED.value
            debit["updated_at"] = datetime.now(UTC).isoformat()
        return DebitResult(
            success=True,
            status=DebitStatus.REVERSED,
            debit_id=str(debit_id),
            reference=debit_reference,
        )

    async def get_refund_status(self, debit_reference: str, refund_id: str | None = None) -> DebitResult:
        """Simulate refund status lookup."""
        del refund_id
        debit = self._debits.get(debit_reference)
        if debit is None:
            return DebitResult(
                success=False,
                status=DebitStatus.FAILED,
                reference=debit_reference,
                error_message="Mock debit not found",
            )
        if debit.get("status") == DebitStatus.REVERSED.value:
            return DebitResult(
                success=True,
                status=DebitStatus.REVERSED,
                debit_id=str(debit.get("id")),
                reference=debit_reference,
            )
        return DebitResult(
            success=True,
            status=DebitStatus.PENDING,
            debit_id=str(debit.get("id")),
            reference=debit_reference,
        )

    async def cancel_mandate(self, mandate_id: str) -> bool:
        """Simulate mandate cancellation."""
        logger.info("mock_cancel_mandate", mandate_id_hash=log_fingerprint(mandate_id))
        return True

    def simulate_debit_success(self, reference: str) -> None:
        """Mark a debit as successful (for testing webhooks)."""
        if reference in self._debits:
            self._debits[reference]["status"] = DebitStatus.SUCCESSFUL.value
            self._debits[reference]["response_code"] = "00"
            self._debits[reference]["updated_at"] = datetime.now(UTC).isoformat()
            self._debits[reference].pop("message", None)

    def simulate_debit_failure(self, reference: str) -> None:
        """Mark a debit as failed (for testing failure paths)."""
        if reference in self._debits:
            self._debits[reference]["status"] = DebitStatus.FAILED.value
            self._debits[reference]["response_code"] = "51"
            self._debits[reference]["message"] = "Debit failed"
            self._debits[reference]["updated_at"] = datetime.now(UTC).isoformat()
