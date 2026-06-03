from types import SimpleNamespace

from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome
from apps.chat.src.agent.workers.transfer.models.types import TransferContext, TransferGates, TransferPayload
from apps.chat.src.agent.workers.transfer.nodes.validation import ValidationStep
from apps.chat.src.agent.workers.transfer.validation.service import ValidationService
from shared.clients.abstractions.direct_debit import BalanceResult


class _BalanceProviderStub:
    def __init__(self, available_balance: float) -> None:
        self.available_balance = available_balance
        self.calls: list[tuple[str, bool]] = []

    async def get_balance(self, account_id: str, real_time: bool = True) -> BalanceResult:
        self.calls.append((account_id, real_time))
        return BalanceResult(
            success=True, available_balance=self.available_balance, ledger_balance=self.available_balance
        )


async def test_transfer_percentage_resolves_against_selected_source_account_balance() -> None:
    step = ValidationStep()
    payload = TransferPayload(
        recipient_name="Mum",
        recipient_account="1234567890",
        recipient_bank_name="Access Bank",
        source_account_id="acc-1",
        source_bank_name="Zenith Bank",
        transfer_percentage=50,
    )
    context = TransferContext(
        phone_number="2348000000000",
        language="en",
        beneficiaries=[],
        accounts=[
            {
                "id": "acc-1",
                "mono_account_id": "mono-1",
                "bank_name": "Zenith Bank",
                "account_name": "Main Account",
                "account_number": "00009384",
                "mandate_status": "ready",
                "mandate_id": "mandate-1",
            }
        ],
    )
    provider = _BalanceProviderStub(available_balance=50000)
    worker_context = SimpleNamespace(
        transaction_repo=None,
        user_id=None,
        validation_service=ValidationService(),
        dd_provider=provider,
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["amount"] == 25000
    assert provider.calls == [("mono-1", True)]


async def test_transfer_all_resolves_against_selected_source_account_balance() -> None:
    step = ValidationStep()
    payload = TransferPayload(
        recipient_name="Tolu",
        recipient_account="1234567890",
        recipient_bank_name="Access Bank",
        source_account_id="acc-1",
        source_bank_name="First Bank",
        transfer_all=True,
    )
    context = TransferContext(
        phone_number="2348000000000",
        language="en",
        beneficiaries=[],
        accounts=[
            {
                "id": "acc-1",
                "mono_account_id": "mono-1",
                "bank_name": "First Bank",
                "account_name": "Savings",
                "account_number": "0334555167",
                "mandate_status": "ready",
                "mandate_id": "mandate-1",
            }
        ],
    )
    provider = _BalanceProviderStub(available_balance=84250)
    worker_context = SimpleNamespace(
        transaction_repo=None,
        user_id=None,
        validation_service=ValidationService(),
        dd_provider=provider,
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["amount"] == 84250
