from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apps.core.src.agent.executors.transfer import TransferExecutor
from shared.clients.abstractions.direct_debit import DebitResult, DebitStatus
from shared.database.enums import TransactionStatusEnum


def _payload() -> dict:
    return {
        "transaction_id": "tx-1",
        "idempotency_key": "idem-1",
        "transfer_data": {
            "amount": 5000,
            "recipient": {
                "account_number": "8162511023",
                "bank_code": "033",
            },
            "source": {
                "account_id": "acc-1",
            },
            "narration": "Test transfer",
        },
        "language": "en",
    }


@pytest.mark.asyncio
async def test_transfer_executor_marks_successful_on_successful_debit() -> None:
    dd_provider = SimpleNamespace(
        initiate_debit_to_beneficiary=AsyncMock(
            return_value=DebitResult(success=True, status=DebitStatus.SUCCESSFUL, reference="ref-1")
        )
    )
    account_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(mandate_id="mandate-1")))
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
    executor = TransferExecutor(
        direct_debit_provider=dd_provider,
        account_repo=account_repo,
        transaction_repo=transaction_repo,
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


@pytest.mark.asyncio
async def test_transfer_executor_keeps_processing_on_pending_debit() -> None:
    dd_provider = SimpleNamespace(
        initiate_debit_to_beneficiary=AsyncMock(
            return_value=DebitResult(success=True, status=DebitStatus.PENDING, reference="ref-2")
        )
    )
    account_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(mandate_id="mandate-1")))
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
    executor = TransferExecutor(
        direct_debit_provider=dd_provider,
        account_repo=account_repo,
        transaction_repo=transaction_repo,
    )

    await executor.handle_transfer(_payload())

    assert transaction_repo.update_status.await_args_list[0].args == (
        "tx-1",
        TransactionStatusEnum.PROCESSING.value,
    )
    assert transaction_repo.update_status.await_args_list[1].args == (
        "tx-1",
        TransactionStatusEnum.PROCESSING.value,
    )


@pytest.mark.asyncio
async def test_transfer_executor_fails_when_source_mandate_missing() -> None:
    dd_provider = SimpleNamespace(initiate_debit_to_beneficiary=AsyncMock())
    account_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(mandate_id=None)))
    transaction_repo = SimpleNamespace(update_status=AsyncMock())
    executor = TransferExecutor(
        direct_debit_provider=dd_provider,
        account_repo=account_repo,
        transaction_repo=transaction_repo,
    )

    await executor.handle_transfer(_payload())

    assert transaction_repo.update_status.await_args_list[-1].args[0:2] == (
        "tx-1",
        TransactionStatusEnum.FAILED.value,
    )
    dd_provider.initiate_debit_to_beneficiary.assert_not_awaited()
