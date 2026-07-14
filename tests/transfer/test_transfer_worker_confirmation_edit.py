import pytest

from banking.runtime.results import TransactionOutcome
from banking.transfers.models.extraction import Correction, CorrectionField, TransferExtractionResult
from banking.transfers.worker import TransferWorker


class _AmountMutationExtractor:
    async def extract(self, *_: object, **__: object) -> TransferExtractionResult:
        return TransferExtractionResult(
            correction=Correction(
                field=CorrectionField.AMOUNT,
                amount_mutation={"steps": [{"operation": "add", "amount": 5000}]},
            )
        )


@pytest.mark.asyncio
async def test_transfer_worker_interprets_pending_confirmation_edit_with_extractor_contract() -> None:
    worker = TransferWorker(
        validation_service=None,
        publisher=None,
        extractor=_AmountMutationExtractor(),
        resolver_provider=None,
        bank_cache=None,
        transaction_repo=None,
    )

    result = await worker.interpret_pending_confirmation_edit(
        payload={
            "amount": 10000,
            "recipient_name": "Adebayo",
            "confirmation": {"summary": "Review", "snapshot": {"amount": 10000}},
        },
        context={
            "phone_number": "2348162511023",
            "language": "en",
            "accounts": [],
            "all_accounts": [],
            "beneficiaries": [],
            "confirmation_task_count": 1,
        },
        user_message="Add 5k",
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["amount"] == 15000
