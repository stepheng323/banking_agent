from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from banking.policy.loader import get_cached_policy, load_policy
from banking.transactions.runtime.executors.transfer import TransferExecutor, _resolve_source_account
from shared.clients.abstractions.direct_debit import DebitResult, DebitStatus
from shared.database.enums import TransactionStatusEnum

CAPABILITY_POLICY_PATH = "banking/policy/defaults/capability_policy.json"
SCHEDULE_DISABLED_MESSAGE = (
    "Scheduled payments are temporarily unavailable. I can still help with immediate transfers, airtime/data purchase, "
    "balances, and transaction queries."
)


class _RedisStub:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.values: dict[str, str] = {}

    async def hset(self, key: str, field: str, value: str) -> None:
        self.hashes.setdefault(key, {})[field] = value

    async def expire(self, key: str, ttl: int) -> None:
        return None

    async def hlen(self, key: str) -> int:
        return len(self.hashes.get(key, {}))

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))


class _SuggestionServiceStub:
    def __init__(self, message: str | None = "Would you like to save Mercy Johnson?") -> None:
        self.check_and_suggest_beneficiary = AsyncMock(return_value=message)


class _DirectTransferTransactionRepo:
    def __init__(self, tx: SimpleNamespace | None = None) -> None:
        self.transactions: dict[str, SimpleNamespace] = {}
        if tx is not None:
            self.transactions[str(tx.id)] = tx
        self.update_status = AsyncMock(side_effect=self._update_status)
        self.stats_calls: list[dict] = []
        self.claim_calls: list[tuple[str, str]] = []
        self.apply_calls: list[tuple[str, str]] = []

    def _ensure_tx(self, transaction_id: str) -> SimpleNamespace:
        tx = self.transactions.get(transaction_id)
        if tx is None:
            tx = SimpleNamespace(
                id=transaction_id,
                user_id="user-1",
                idempotency_key="idem-1",
                transaction_id=None,
                status=TransactionStatusEnum.PENDING.value,
                provider_status=None,
                provider_response=None,
                provider_error_code=None,
                error_message=None,
                service_metadata={},
            )
            self.transactions[transaction_id] = tx
        return tx

    async def _update_status(
        self,
        transaction_id: str,
        status: str,
        error_message: str | None = None,
        *,
        provider_transaction_id: str | None = None,
        provider_status: str | None = None,
        provider_response: dict | None = None,
        provider_error_code: str | None = None,
    ) -> SimpleNamespace:
        tx = self._ensure_tx(transaction_id)
        tx.status = status
        if error_message:
            tx.error_message = error_message
        if provider_transaction_id:
            tx.transaction_id = provider_transaction_id
        if provider_status:
            tx.provider_status = provider_status
        if provider_response is not None:
            tx.provider_response = provider_response
        if provider_error_code:
            tx.provider_error_code = provider_error_code
        return tx

    async def get_by_id(self, transaction_id: str) -> SimpleNamespace:
        return self._ensure_tx(transaction_id)

    async def claim_for_direct_transfer(
        self,
        transaction_id: str,
        *,
        provider_reference: str,
        service_metadata: dict | None = None,
    ) -> SimpleNamespace | None:
        tx = self._ensure_tx(transaction_id)
        if tx.status in {TransactionStatusEnum.SUCCESSFUL.value, TransactionStatusEnum.FAILED.value, "reversed"}:
            return None
        if tx.status == TransactionStatusEnum.PROCESSING.value and tx.transaction_id:
            return None
        self.claim_calls.append((transaction_id, provider_reference))
        tx.provider_status = "direct_transfer_claimed"
        tx.transaction_id = provider_reference
        if service_metadata:
            tx.service_metadata = {**(getattr(tx, "service_metadata", {}) or {}), **service_metadata}
        await self.update_status(transaction_id, TransactionStatusEnum.PROCESSING.value)
        return tx

    async def apply_direct_transfer_result(
        self,
        transaction_id: str,
        *,
        result: DebitResult,
        provider_reference: str,
    ) -> tuple[SimpleNamespace, str]:
        tx = self._ensure_tx(transaction_id)
        if tx.status in {TransactionStatusEnum.SUCCESSFUL.value, TransactionStatusEnum.FAILED.value, "reversed"}:
            return tx, "skipped"
        self.apply_calls.append((transaction_id, provider_reference))
        provider_response = result.provider_response or {}
        provider_error_code = provider_response.get("response_code") or provider_response.get("error_code")
        provider_transaction_id = result.debit_id or result.reference or provider_reference
        kwargs = {
            "provider_transaction_id": provider_transaction_id,
            "provider_status": result.status.value,
            "provider_response": provider_response,
            "provider_error_code": None if provider_error_code is None else str(provider_error_code),
        }
        if result.success and result.status == DebitStatus.SUCCESSFUL:
            await self.update_status(transaction_id, TransactionStatusEnum.SUCCESSFUL.value, **kwargs)
            return tx, "successful"
        if result.success and result.status in {DebitStatus.PENDING, DebitStatus.PROCESSING}:
            await self.update_status(transaction_id, TransactionStatusEnum.PROCESSING.value, **kwargs)
            return tx, "processing"
        await self.update_status(
            transaction_id,
            TransactionStatusEnum.FAILED.value,
            error_message=result.error_message or "Transfer failed",
            **kwargs,
        )
        return tx, "failed"

    async def get_successful_transfer_personality_stats(self, user_id: str, **kwargs) -> dict:
        self.stats_calls.append({"user_id": user_id, **kwargs})
        return {
            "prior_successful_transfer_count": 0,
            "prior_max_successful_transfer_amount": 0,
            "recipient_success_count_90d": 0,
        }


def _payload() -> dict:
    return {
        "transaction_id": "tx-1",
        "idempotency_key": "idem-1",
        "phone_number": "2348162511023",
        "channel": "telegram",
        "channel_identity": "927331985",
        "transfer_data": {
            "amount": 5000,
            "recipient": {
                "account_number": "8162511023",
                "bank_code": "033",
                "name": "Mercy Johnson",
                "bank_name": "Opay",
            },
            "source": {
                "account_id": "acc-1",
                "account_number": "1234567890",
                "account_name": "Gaines",
                "bank_name": "Zenith Bank",
            },
            "narration": "Test transfer",
        },
        "language": "en",
        "async_group": {
            "async_group_id": "group-1",
            "async_group_size": 1,
            "async_group_kind": "single",
            "async_group_index": 1,
        },
    }


def _install_disabled_schedule_policy(tmp_path: Path) -> None:
    raw = load_policy(CAPABILITY_POLICY_PATH).model_dump()
    raw["capability_matrix"]["schedule"]["enabled"] = False
    raw["capability_matrix"]["schedule"]["limitation_message"] = SCHEDULE_DISABLED_MESSAGE
    policy_path = tmp_path / "capability_policy_schedule_disabled.json"
    policy_path.write_text(json.dumps(raw, ensure_ascii=True), encoding="utf-8")
    get_cached_policy(path=str(policy_path), force_reload=True)


def _reset_policy_cache() -> None:
    get_cached_policy(path=CAPABILITY_POLICY_PATH, force_reload=True)


@pytest.mark.asyncio
async def test_source_account_provider_reference_uses_external_account_lookup() -> None:
    provider_account = SimpleNamespace(mandate_id="mandate-provider")
    account_repo = SimpleNamespace(
        get_by_account_id=AsyncMock(return_value=provider_account),
        get_by_id=AsyncMock(side_effect=AssertionError("provider ID must not use the UUID lookup")),
    )

    resolved = await _resolve_source_account(account_repo, "seed-user-acct-2")

    assert resolved is provider_account
    account_repo.get_by_account_id.assert_awaited_once_with("seed-user-acct-2")
    account_repo.get_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_transfer_executor_single_success_delivers_and_enqueues_receipt() -> None:
    dd_provider = SimpleNamespace(
        initiate_debit_to_beneficiary=AsyncMock(
            return_value=DebitResult(
                success=True,
                status=DebitStatus.SUCCESSFUL,
                debit_id="debit-1",
                reference="ref-1",
                provider_response={
                    "id": "debit-1",
                    "status": "successful",
                    "reference": "ref-1",
                    "response_code": "00",
                },
            )
        )
    )
    account_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(mandate_id="mandate-1")))
    transaction_repo = _DirectTransferTransactionRepo()
    publisher = SimpleNamespace(publish=AsyncMock())
    delivery_service = SimpleNamespace(deliver_text=AsyncMock(), deliver_intents=AsyncMock())
    executor = TransferExecutor(
        direct_debit_provider=dd_provider,
        account_repo=account_repo,
        transaction_repo=transaction_repo,
        publisher=publisher,
        delivery_service=delivery_service,
        redis_client=_RedisStub(),
    )

    await executor.handle_transfer(_payload())

    assert transaction_repo.update_status.await_args_list[0].args == (
        "tx-1",
        TransactionStatusEnum.PROCESSING.value,
    )
    assert transaction_repo.update_status.await_args_list[1].args == (
        "tx-1",
        TransactionStatusEnum.SUCCESSFUL.value,
    )
    assert transaction_repo.update_status.await_args_list[1].kwargs == {
        "provider_transaction_id": "debit-1",
        "provider_status": DebitStatus.SUCCESSFUL.value,
        "provider_response": {"id": "debit-1", "status": "successful", "reference": "ref-1", "response_code": "00"},
        "provider_error_code": "00",
    }
    assert delivery_service.deliver_text.await_args.kwargs["phone_number"] == "927331985"
    delivered_text = delivery_service.deliver_text.await_args.kwargs["text"]
    assert "has been sent to Mercy Johnson" in delivered_text
    assert "Transaction ID" not in delivered_text
    assert "debit-1" not in delivered_text
    publisher.publish.assert_not_awaited()
    delivery_service.deliver_intents.assert_awaited_once()
    receipt_offer = delivery_service.deliver_intents.await_args.kwargs
    assert receipt_offer["phone_number"] == "927331985"
    assert receipt_offer["channel"] == "telegram"
    assert receipt_offer["intents"][0].title == "Would you like a receipt image for this transfer?"
    assert receipt_offer["intents"][0].options[0]["title"] == "Send receipt image"
    assert receipt_offer["dedupe_key"] == "receipt-choice:tx-1"


@pytest.mark.asyncio
async def test_transfer_executor_single_success_sends_visible_beneficiary_suggestion() -> None:
    dd_provider = SimpleNamespace(
        initiate_debit_to_beneficiary=AsyncMock(
            return_value=DebitResult(
                success=True,
                status=DebitStatus.SUCCESSFUL,
                debit_id="debit-1",
                reference="ref-1",
                provider_response={
                    "id": "debit-1",
                    "status": "successful",
                    "reference": "ref-1",
                    "response_code": "00",
                },
            )
        )
    )
    account_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(mandate_id="mandate-1")))
    transaction_repo = _DirectTransferTransactionRepo()
    delivery_service = SimpleNamespace(deliver_text=AsyncMock(), deliver_intents=AsyncMock())
    suggestion_service = _SuggestionServiceStub()
    executor = TransferExecutor(
        direct_debit_provider=dd_provider,
        account_repo=account_repo,
        transaction_repo=transaction_repo,
        delivery_service=delivery_service,
        redis_client=_RedisStub(),
        beneficiary_suggestion_service=suggestion_service,
    )

    await executor.handle_transfer(_payload())

    assert delivery_service.deliver_text.await_count == 2
    assert "has been sent to Mercy Johnson" in delivery_service.deliver_text.await_args_list[0].kwargs["text"]
    assert delivery_service.deliver_text.await_args_list[1].kwargs["text"] == "Would you like to save Mercy Johnson?"
    receipt_job = delivery_service.deliver_intents.await_args.kwargs["intents"][0].actionable_payload["receipt_job"]
    assert "beneficiary_suggestion_message" not in receipt_job
    suggestion_service.check_and_suggest_beneficiary.assert_awaited_once()


@pytest.mark.asyncio
async def test_transfer_executor_batch_success_does_not_suggest_beneficiary() -> None:
    dd_provider = SimpleNamespace(
        initiate_debit_to_beneficiary=AsyncMock(
            return_value=DebitResult(
                success=True,
                status=DebitStatus.SUCCESSFUL,
                debit_id="debit-1",
                reference="ref-1",
                provider_response={
                    "id": "debit-1",
                    "status": "successful",
                    "reference": "ref-1",
                    "response_code": "00",
                },
            )
        )
    )
    account_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(mandate_id="mandate-1")))
    transaction_repo = _DirectTransferTransactionRepo()
    delivery_service = SimpleNamespace(deliver_text=AsyncMock(), deliver_intents=AsyncMock())
    suggestion_service = _SuggestionServiceStub()
    payload = _payload()
    payload["async_group"] = {
        "async_group_id": "group-transfer-batch",
        "async_group_size": 2,
        "async_group_kind": "multi_transfer",
        "async_group_index": 1,
    }
    executor = TransferExecutor(
        direct_debit_provider=dd_provider,
        account_repo=account_repo,
        transaction_repo=transaction_repo,
        delivery_service=delivery_service,
        redis_client=_RedisStub(),
        beneficiary_suggestion_service=suggestion_service,
    )

    await executor.handle_transfer(payload)

    suggestion_service.check_and_suggest_beneficiary.assert_not_awaited()


@pytest.mark.asyncio
async def test_transfer_executor_success_uses_celebratory_tone_for_first_transfer() -> None:
    dd_provider = SimpleNamespace(
        initiate_debit_to_beneficiary=AsyncMock(
            return_value=DebitResult(
                success=True,
                status=DebitStatus.SUCCESSFUL,
                debit_id="debit-1",
                reference="ref-1",
                provider_response={
                    "id": "debit-1",
                    "status": "successful",
                    "reference": "ref-1",
                    "response_code": "00",
                },
            )
        )
    )
    account_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(mandate_id="mandate-1")))
    transaction_repo = _DirectTransferTransactionRepo()
    delivery_service = SimpleNamespace(deliver_text=AsyncMock(), deliver_intents=AsyncMock())
    executor = TransferExecutor(
        direct_debit_provider=dd_provider,
        account_repo=account_repo,
        transaction_repo=transaction_repo,
        delivery_service=delivery_service,
        redis_client=_RedisStub(),
    )

    await executor.handle_transfer(_payload())

    assert delivery_service.deliver_text.await_args.kwargs["text"].startswith("All set. ₦5,000")
    assert transaction_repo.stats_calls[0]["exclude_transaction_id"] == "tx-1"
    assert transaction_repo.stats_calls[0]["exclude_idempotency_key"] == "idem-1"


@pytest.mark.asyncio
async def test_transfer_executor_processing_persists_provider_metadata() -> None:
    dd_provider = SimpleNamespace(
        initiate_debit_to_beneficiary=AsyncMock(
            return_value=DebitResult(
                success=True,
                status=DebitStatus.PROCESSING,
                debit_id="debit-pending-1",
                reference="ref-processing-1",
                provider_response={"id": "debit-pending-1", "status": "processing", "reference": "ref-processing-1"},
            )
        )
    )
    account_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(mandate_id="mandate-1")))
    transaction_repo = _DirectTransferTransactionRepo()
    delivery_service = SimpleNamespace(deliver_text=AsyncMock())
    executor = TransferExecutor(
        direct_debit_provider=dd_provider,
        account_repo=account_repo,
        transaction_repo=transaction_repo,
        delivery_service=delivery_service,
        redis_client=_RedisStub(),
    )

    await executor.handle_transfer(_payload())

    assert transaction_repo.update_status.await_args_list[1].args == (
        "tx-1",
        TransactionStatusEnum.PROCESSING.value,
    )
    assert transaction_repo.update_status.await_args_list[1].kwargs == {
        "provider_transaction_id": "debit-pending-1",
        "provider_status": DebitStatus.PROCESSING.value,
        "provider_response": {
            "id": "debit-pending-1",
            "status": "processing",
            "reference": "ref-processing-1",
        },
        "provider_error_code": None,
    }
    assert "is processing" in delivery_service.deliver_text.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_transfer_executor_processing_sends_visible_beneficiary_suggestion() -> None:
    dd_provider = SimpleNamespace(
        initiate_debit_to_beneficiary=AsyncMock(
            return_value=DebitResult(
                success=True,
                status=DebitStatus.PROCESSING,
                debit_id="debit-pending-1",
                reference="ref-processing-1",
                provider_response={"id": "debit-pending-1", "status": "processing", "reference": "ref-processing-1"},
            )
        )
    )
    account_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(mandate_id="mandate-1")))
    transaction_repo = _DirectTransferTransactionRepo()
    delivery_service = SimpleNamespace(deliver_text=AsyncMock())
    suggestion_service = _SuggestionServiceStub()
    executor = TransferExecutor(
        direct_debit_provider=dd_provider,
        account_repo=account_repo,
        transaction_repo=transaction_repo,
        delivery_service=delivery_service,
        redis_client=_RedisStub(),
        beneficiary_suggestion_service=suggestion_service,
    )

    await executor.handle_transfer(_payload())

    assert delivery_service.deliver_text.await_count == 2
    assert "is processing" in delivery_service.deliver_text.await_args_list[0].kwargs["text"]
    assert delivery_service.deliver_text.await_args_list[1].kwargs["text"] == "Would you like to save Mercy Johnson?"


@pytest.mark.asyncio
async def test_transfer_executor_failed_debit_persists_response_code() -> None:
    dd_provider = SimpleNamespace(
        initiate_debit_to_beneficiary=AsyncMock(
            return_value=DebitResult(
                success=False,
                status=DebitStatus.FAILED,
                debit_id="debit-failed-1",
                reference="ref-failed-1",
                error_message="Insufficient funds",
                provider_response={
                    "id": "debit-failed-1",
                    "status": "failed",
                    "reference": "ref-failed-1",
                    "response_code": "51",
                    "message": "Insufficient funds",
                },
            )
        )
    )
    account_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(mandate_id="mandate-1")))
    transaction_repo = _DirectTransferTransactionRepo()
    delivery_service = SimpleNamespace(deliver_text=AsyncMock())
    executor = TransferExecutor(
        direct_debit_provider=dd_provider,
        account_repo=account_repo,
        transaction_repo=transaction_repo,
        delivery_service=delivery_service,
        redis_client=_RedisStub(),
    )

    await executor.handle_transfer(_payload())

    assert transaction_repo.update_status.await_args_list[1].args == (
        "tx-1",
        TransactionStatusEnum.FAILED.value,
    )
    assert transaction_repo.update_status.await_args_list[1].kwargs == {
        "error_message": "Insufficient funds",
        "provider_transaction_id": "debit-failed-1",
        "provider_status": DebitStatus.FAILED.value,
        "provider_response": {
            "id": "debit-failed-1",
            "status": "failed",
            "reference": "ref-failed-1",
            "response_code": "51",
            "message": "Insufficient funds",
        },
        "provider_error_code": "51",
    }
    assert "Insufficient funds" in delivery_service.deliver_text.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_transfer_executor_exception_uses_safe_user_error() -> None:
    dd_provider = SimpleNamespace(
        initiate_debit_to_beneficiary=AsyncMock(side_effect=RuntimeError("raw provider token leaked"))
    )
    account_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(mandate_id="mandate-1")))
    transaction_repo = _DirectTransferTransactionRepo()
    delivery_service = SimpleNamespace(deliver_text=AsyncMock())
    executor = TransferExecutor(
        direct_debit_provider=dd_provider,
        account_repo=account_repo,
        transaction_repo=transaction_repo,
        delivery_service=delivery_service,
        redis_client=_RedisStub(),
    )

    await executor.handle_transfer(_payload())

    assert transaction_repo.update_status.await_args_list[1].args == (
        "tx-1",
        TransactionStatusEnum.PROCESSING.value,
    )
    assert transaction_repo.update_status.await_args_list[1].kwargs["provider_status"] == DebitStatus.PROCESSING.value
    assert "raw provider token leaked" not in delivery_service.deliver_text.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_transfer_executor_grouped_legs_emit_one_summary_on_last_completion() -> None:
    dd_provider = SimpleNamespace(
        initiate_debit_to_beneficiary=AsyncMock(
            side_effect=[
                DebitResult(success=True, status=DebitStatus.SUCCESSFUL, reference="ref-1"),
                DebitResult(success=False, status=DebitStatus.FAILED, error_message="Provider down"),
            ]
        )
    )
    account_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(mandate_id="mandate-1")))
    transaction_repo = _DirectTransferTransactionRepo()
    publisher = SimpleNamespace(publish=AsyncMock())
    delivery_service = SimpleNamespace(deliver_text=AsyncMock())
    redis_client = _RedisStub()
    executor = TransferExecutor(
        direct_debit_provider=dd_provider,
        account_repo=account_repo,
        transaction_repo=transaction_repo,
        publisher=publisher,
        delivery_service=delivery_service,
        redis_client=redis_client,
    )

    first = _payload()
    first["transaction_id"] = "tx-1"
    first["async_group"] = {
        "async_group_id": "group-2",
        "async_group_size": 2,
        "async_group_kind": "multi_transfer",
        "async_group_index": 1,
    }

    second = _payload()
    second["transaction_id"] = "tx-2"
    second["transfer_data"] = {
        **second["transfer_data"],
        "amount": 8000,
        "recipient": {
            "account_number": "8162515261",
            "bank_code": "011",
            "name": "Tolu Adedayo",
            "bank_name": "First Bank",
        },
    }
    second["async_group"] = {
        "async_group_id": "group-2",
        "async_group_size": 2,
        "async_group_kind": "multi_transfer",
        "async_group_index": 2,
    }

    await executor.handle_transfer(first)
    delivery_service.deliver_text.assert_not_awaited()
    publisher.publish.assert_not_awaited()

    await executor.handle_transfer(second)

    assert delivery_service.deliver_text.await_count == 1
    summary_text = delivery_service.deliver_text.await_args.kwargs["text"]
    assert "Transfers Complete" in summary_text
    assert "Mercy Johnson" in summary_text
    assert "Tolu Adedayo" in summary_text
    assert "Some transactions could not be completed." in summary_text
    publisher.publish.assert_not_awaited()

    stored = json.loads(redis_client.hashes["async-group:group-2:legs"]["1"])
    assert stored["payload"]["final_status"] == "success"


@pytest.mark.asyncio
async def test_transfer_executor_grouped_processing_leg_still_emits_summary() -> None:
    dd_provider = SimpleNamespace(
        initiate_debit_to_beneficiary=AsyncMock(
            side_effect=[
                DebitResult(success=True, status=DebitStatus.SUCCESSFUL, reference="ref-1"),
                DebitResult(success=True, status=DebitStatus.PROCESSING, reference="ref-2"),
            ]
        )
    )
    account_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(mandate_id="mandate-1")))
    transaction_repo = _DirectTransferTransactionRepo()
    delivery_service = SimpleNamespace(deliver_text=AsyncMock())
    redis_client = _RedisStub()
    executor = TransferExecutor(
        direct_debit_provider=dd_provider,
        account_repo=account_repo,
        transaction_repo=transaction_repo,
        delivery_service=delivery_service,
        redis_client=redis_client,
    )

    first = _payload()
    first["transaction_id"] = "tx-1"
    first["async_group"] = {
        "async_group_id": "group-3",
        "async_group_size": 2,
        "async_group_kind": "multi_transfer",
        "async_group_index": 1,
    }

    second = _payload()
    second["transaction_id"] = "tx-2"
    second["transfer_data"] = {
        **second["transfer_data"],
        "amount": 8000,
        "recipient": {
            "account_number": "8162515261",
            "bank_code": "011",
            "name": "Tolu Adedayo",
            "bank_name": "First Bank",
        },
    }
    second["async_group"] = {
        "async_group_id": "group-3",
        "async_group_size": 2,
        "async_group_kind": "multi_transfer",
        "async_group_index": 2,
    }

    await executor.handle_transfer(first)
    delivery_service.deliver_text.assert_not_awaited()

    await executor.handle_transfer(second)

    assert delivery_service.deliver_text.await_count == 1
    summary_text = delivery_service.deliver_text.await_args.kwargs["text"]
    assert "Transfers Update" in summary_text
    assert "Mercy Johnson" in summary_text
    assert "Tolu Adedayo" in summary_text
    assert "Some transactions completed; others are still being processed." in summary_text

    stored = json.loads(redis_client.hashes["async-group:group-3:legs"]["2"])
    assert stored["payload"]["final_status"] == "processing"


@pytest.mark.asyncio
async def test_transfer_executor_suppresses_duplicate_terminal_transaction() -> None:
    dd_provider = SimpleNamespace(initiate_debit_to_beneficiary=AsyncMock())
    account_repo = SimpleNamespace(get_by_id=AsyncMock())
    transaction_repo = _DirectTransferTransactionRepo(
        SimpleNamespace(
            id="tx-1",
            status=TransactionStatusEnum.SUCCESSFUL.value,
            transaction_id="debit-1",
            idempotency_key="idem-1",
            user_id="user-1",
            service_metadata={},
        )
    )
    delivery_service = SimpleNamespace(deliver_text=AsyncMock())
    executor = TransferExecutor(
        direct_debit_provider=dd_provider,
        account_repo=account_repo,
        transaction_repo=transaction_repo,
        delivery_service=delivery_service,
        redis_client=_RedisStub(),
    )

    await executor.handle_transfer(_payload())

    dd_provider.initiate_debit_to_beneficiary.assert_not_awaited()
    transaction_repo.update_status.assert_not_awaited()
    delivery_service.deliver_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_transfer_executor_suppresses_duplicate_processing_with_provider_reference() -> None:
    dd_provider = SimpleNamespace(initiate_debit_to_beneficiary=AsyncMock())
    account_repo = SimpleNamespace(get_by_id=AsyncMock())
    transaction_repo = _DirectTransferTransactionRepo(
        SimpleNamespace(
            id="tx-1",
            status=TransactionStatusEnum.PROCESSING.value,
            transaction_id="debit-processing-1",
            idempotency_key="idem-1",
            user_id="user-1",
            service_metadata={},
        )
    )
    delivery_service = SimpleNamespace(deliver_text=AsyncMock())
    executor = TransferExecutor(
        direct_debit_provider=dd_provider,
        account_repo=account_repo,
        transaction_repo=transaction_repo,
        delivery_service=delivery_service,
        redis_client=_RedisStub(),
    )

    await executor.handle_transfer(_payload())

    dd_provider.initiate_debit_to_beneficiary.assert_not_awaited()
    transaction_repo.update_status.assert_not_awaited()
    delivery_service.deliver_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_transfer_executor_duplicate_claim_queues_reference_reconciliation() -> None:
    dd_provider = SimpleNamespace(initiate_debit_to_beneficiary=AsyncMock())
    account_repo = SimpleNamespace(get_by_id=AsyncMock())
    transaction_repo = _DirectTransferTransactionRepo(
        SimpleNamespace(
            id="tx-1",
            status=TransactionStatusEnum.PROCESSING.value,
            transaction_id="idem-1",
            idempotency_key="idem-1",
            user_id="user-1",
            service_metadata={},
        )
    )
    publisher = SimpleNamespace(publish=AsyncMock())
    executor = TransferExecutor(
        direct_debit_provider=dd_provider,
        account_repo=account_repo,
        transaction_repo=transaction_repo,
        publisher=publisher,
        delivery_service=SimpleNamespace(deliver_text=AsyncMock()),
        redis_client=_RedisStub(),
    )

    await executor.handle_transfer(_payload())

    dd_provider.initiate_debit_to_beneficiary.assert_not_awaited()
    publisher.publish.assert_awaited_once_with(
        topic="direct_transfer.reconcile",
        message={"transaction_id": "tx-1", "reference": "idem-1"},
    )


@pytest.mark.asyncio
async def test_scheduled_transfer_executor_blocks_when_schedule_domain_disabled(tmp_path: Path) -> None:
    _install_disabled_schedule_policy(tmp_path)
    try:
        dd_provider = SimpleNamespace(initiate_debit_to_beneficiary=AsyncMock())
        account_repo = SimpleNamespace(get_by_id=AsyncMock())
        transaction_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=None), update_status=AsyncMock())
        delivery_service = SimpleNamespace(deliver_text=AsyncMock())
        executor = TransferExecutor(
            direct_debit_provider=dd_provider,
            account_repo=account_repo,
            transaction_repo=transaction_repo,
            delivery_service=delivery_service,
            redis_client=_RedisStub(),
        )
        payload = _payload()
        payload["scheduled_meta"] = {
            "schedule_id": "sch-1",
            "run_source": "scheduled",
            "attempt": 1,
        }

        await executor.handle_transfer(payload)

        dd_provider.initiate_debit_to_beneficiary.assert_not_awaited()
        account_repo.get_by_id.assert_not_awaited()
        transaction_repo.update_status.assert_awaited_once_with(
            "tx-1",
            TransactionStatusEnum.FAILED.value,
            SCHEDULE_DISABLED_MESSAGE,
        )
        delivery_service.deliver_text.assert_awaited_once()
        assert SCHEDULE_DISABLED_MESSAGE in delivery_service.deliver_text.await_args.kwargs["text"]
    finally:
        _reset_policy_cache()
