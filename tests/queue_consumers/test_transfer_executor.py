from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apps.chat.src.agent.executors.transfer import TransferExecutor
from shared.clients.abstractions.direct_debit import DebitResult, DebitStatus
from shared.database.enums import TransactionStatusEnum


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


@pytest.mark.asyncio
async def test_transfer_executor_single_success_delivers_and_enqueues_receipt() -> None:
    dd_provider = SimpleNamespace(
        initiate_debit_to_beneficiary=AsyncMock(
            return_value=DebitResult(
                success=True,
                status=DebitStatus.SUCCESSFUL,
                debit_id="debit-1",
                reference="ref-1",
                provider_response={"id": "debit-1", "status": "successful", "reference": "ref-1", "response_code": "00"},
            )
        )
    )
    account_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(mandate_id="mandate-1")))
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
    publisher = SimpleNamespace(publish=AsyncMock())
    delivery_service = SimpleNamespace(deliver_text=AsyncMock())
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
    assert "Transfer successful" in delivery_service.deliver_text.await_args.kwargs["text"]
    publisher.publish.assert_awaited_once()
    assert publisher.publish.await_args.kwargs["topic"] == "receipt.process"
    assert publisher.publish.await_args.kwargs["message"]["transaction_reference"] == "debit-1"


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
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
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
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
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
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
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
    error_message = transaction_repo.update_status.await_args_list[1].kwargs["error_message"]
    assert error_message == "Transfer could not be completed. Please try again."
    assert "raw provider token leaked" not in error_message
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
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
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
    assert "Some transactions completed, but others failed." in summary_text
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
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
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
    assert "awaiting provider confirmation" in summary_text
    assert "You'll be notified when the final update arrives." in summary_text

    stored = json.loads(redis_client.hashes["async-group:group-3:legs"]["2"])
    assert stored["payload"]["final_status"] == "processing"


@pytest.mark.asyncio
async def test_transfer_executor_suppresses_duplicate_terminal_transaction() -> None:
    dd_provider = SimpleNamespace(initiate_debit_to_beneficiary=AsyncMock())
    account_repo = SimpleNamespace(get_by_id=AsyncMock())
    transaction_repo = SimpleNamespace(
        get_by_id=AsyncMock(
            return_value=SimpleNamespace(
                status=TransactionStatusEnum.SUCCESSFUL.value,
                transaction_id="debit-1",
            )
        ),
        update_status=AsyncMock(),
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
    transaction_repo = SimpleNamespace(
        get_by_id=AsyncMock(
            return_value=SimpleNamespace(
                status=TransactionStatusEnum.PROCESSING.value,
                transaction_id="debit-processing-1",
            )
        ),
        update_status=AsyncMock(),
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
