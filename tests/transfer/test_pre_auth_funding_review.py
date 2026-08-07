from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from banking.runtime.results import TransactionOutcome
from banking.transfers.models.types import TransferContext, TransferGates, TransferPayload
from banking.transfers.nodes.confirmation import ConfirmationStep
from banking.transfers.nodes.execution import ExecutionStep, _database_account_id
from banking.transfers.nodes.funding import FundingStep

ACCESS_ID = "11111111-1111-1111-1111-111111111111"
GTB_ID = "22222222-2222-2222-2222-222222222222"


class _BalanceProvider:
    def __init__(self, balances: dict[str, Decimal]) -> None:
        self.balances = balances

    async def get_balance(self, account_id: str, real_time: bool = True) -> SimpleNamespace:
        del real_time
        return SimpleNamespace(
            success=True,
            available_balance=self.balances.get(account_id, Decimal("0.00")),
        )


def _context() -> TransferContext:
    return TransferContext(
        phone_number="2348000000000",
        language="en",
        accounts=[
            {
                "id": "acc-1",
                "mono_account_id": "mono-1",
                "account_number": "6000000001",
                "bank_name": "Access Bank",
                "mandate_status": "ready",
                "is_default": True,
            }
        ],
        all_accounts=[
            {
                "id": "acc-1",
                "mono_account_id": "mono-1",
                "account_number": "6000000001",
                "bank_name": "Access Bank",
                "mandate_status": "ready",
                "is_default": True,
            }
        ],
    )


def _context_two_accounts() -> TransferContext:
    access = {
        "id": ACCESS_ID,
        "mono_account_id": "mono-1",
        "account_number": "6000000001",
        "bank_name": "Access Bank",
        "mandate_status": "ready",
        "is_default": True,
    }
    gtb = {
        "id": GTB_ID,
        "mono_account_id": "mono-2",
        "account_number": "6000000002",
        "bank_name": "GTBank",
        "mandate_status": "ready",
        "is_default": False,
    }
    return TransferContext(
        phone_number="2348000000000",
        language="en",
        accounts=[access, gtb],
        all_accounts=[access, gtb],
    )


def _payload(
    *,
    amount: Decimal = Decimal("40000.00"),
    funding_plan: dict | None = None,
    source_account_id: str = "acc-1",
) -> TransferPayload:
    return TransferPayload(
        amount=amount,
        recipient_name="Adebayo",
        recipient_resolved_name="Tolu Adebayo",
        recipient_account="2010000001",
        recipient_bank_name="Access Bank",
        recipient_bank_code="044",
        source_account_id=source_account_id,
        source_bank_name="Access Bank",
        source_account_number="6000000001",
        idempotency_key="idem-funding-review",
        funding_plan=funding_plan,
    )


def _funding_plan(*, amount: Decimal = Decimal("40000.00")) -> dict:
    return {
        "transfer_amount": amount,
        "total_funded": amount,
        "is_sufficient": True,
        "is_single_source": True,
        "planned_for_amount": f"{amount:.2f}",
        "planned_for_source_account_id": "acc-1",
        "planned_for_source_accounts": [],
        "planned_for_use_dual_accounts": False,
        "planned_for_explicit_split": {},
        "steps": [
            {
                "account_id": "acc-1",
                "account_number": "6000000001",
                "bank_name": "Access Bank",
                "amount": amount,
                "sequence": 1,
            }
        ],
    }


def test_runtime_provider_account_id_maps_to_database_uuid_at_persistence_boundary() -> None:
    database_id = UUID("11111111-1111-1111-1111-111111111111")
    assert _database_account_id("seed-user-acct-2", {"seed-user-acct-2": database_id}, normalize=True) == database_id
    assert _database_account_id("seed-user-acct-2", {}, normalize=False) == "seed-user-acct-2"


@pytest.mark.asyncio
async def test_auto_pooled_single_transfer_requires_funding_suggestion_acceptance() -> None:
    result = await FundingStep().execute(
        _payload(amount=Decimal("60000.00"), funding_plan=None, source_account_id=ACCESS_ID),
        _context_two_accounts(),
        TransferGates(),
        worker_context=SimpleNamespace(
            dd_provider=_BalanceProvider({"mono-1": Decimal("30000.00"), "mono-2": Decimal("30000.00")})
        ),
    )

    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.details["insufficient_reason"] == "pool_approval_required"
    assert result.details["review_state"] == "funding_adjustment"
    assert "funding_plan" in result.details
    assert result.details["interrupt_metadata"] == {
        "intent": "single_funding_source_choice",
        "anchor_source_ids": [ACCESS_ID],
        "candidate_source_ids": [GTB_ID],
        "remaining_amount": "30000.00",
        "primary_contribution": "30000.00",
    }
    assert "This transfer needs ₦60,000," in (result.prompt or "")
    assert "Which would you like to use?" in (result.prompt or "")


@pytest.mark.asyncio
async def test_explicit_single_transfer_pool_choice_advances_without_second_funding_prompt() -> None:
    payload = _payload(
        amount=Decimal("60000.00"),
        funding_plan=None,
        source_account_id=ACCESS_ID,
    ).model_copy(
        update={
            "source_accounts": ["Access Bank", "GTBank"],
            "use_dual_accounts": True,
            "explicit_split": {
                "Access Bank": Decimal("30000.00"),
                "GTBank": Decimal("30000.00"),
            },
            "source_affinity_mode": "explicit",
        }
    )

    result = await FundingStep().execute(
        payload,
        _context_two_accounts(),
        TransferGates(),
        worker_context=SimpleNamespace(
            dd_provider=_BalanceProvider({"mono-1": Decimal("30000.00"), "mono-2": Decimal("30000.00")})
        ),
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.prompt is None
    assert result.patch["funding_plan"]["is_single_source"] is False
    assert [step["amount"] for step in result.patch["funding_plan"]["steps"]] == [
        Decimal("30000.00"),
        Decimal("30000.00"),
    ]


@pytest.mark.asyncio
async def test_confirmation_blocks_missing_funding_plan_before_pin() -> None:
    result = await ConfirmationStep().execute(
        _payload(funding_plan=None),
        _context(),
        TransferGates(),
        worker_context=SimpleNamespace(dd_provider=_BalanceProvider({"mono-1": Decimal("50000.00")})),
    )

    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.details["review_state"] == "funding_adjustment"
    assert result.details["funding_plan_status"] == "missing"


@pytest.mark.asyncio
async def test_execution_rejects_stale_funding_plan_after_auth() -> None:
    result = await ExecutionStep().execute(
        _payload(amount=Decimal("45000.00"), funding_plan=_funding_plan(amount=Decimal("40000.00"))),
        _context(),
        TransferGates(confirmation_confirmed=True, pin_verified=True),
        worker_context=SimpleNamespace(dd_provider=_BalanceProvider({"mono-1": Decimal("50000.00")})),
    )

    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.details["review_state"] == "funding_adjustment"
    assert result.details["funding_plan_status"] in {"stale", "insufficient"}
    assert result.patch["funding_plan"] is None


@pytest.mark.asyncio
async def test_execution_rejects_balance_drift_after_confirmation() -> None:
    result = await ExecutionStep().execute(
        _payload(funding_plan=_funding_plan(amount=Decimal("40000.00"))),
        _context(),
        TransferGates(confirmation_confirmed=True, pin_verified=True),
        worker_context=SimpleNamespace(dd_provider=_BalanceProvider({"mono-1": Decimal("30000.00")})),
    )

    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.details["review_state"] == "funding_adjustment"
    assert result.details["funding_plan_status"] == "balance_drift"
    assert result.patch["funding_plan"] is None
