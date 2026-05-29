from uuid import uuid4

import pytest

from banking.transfers.funding.batch_models import SourceAffinity, TransferDemand
from banking.transfers.funding.coordinator import BatchFundingCoordinator
from shared.clients.abstractions.direct_debit import BalanceResult


class _MockDirectDebitProvider:
    def __init__(self, balances: dict[str, float]) -> None:
        self.balances = balances

    async def get_balance(self, account_id: str, real_time: bool = True) -> BalanceResult:
        del real_time
        amount = float(self.balances.get(account_id, 0.0))
        return BalanceResult(success=True, available_balance=amount, ledger_balance=amount, currency="NGN")


def _account(bank_name: str, account_ref: str, *, is_default: bool = False) -> dict:
    return {
        "id": str(uuid4()),
        "account_id": account_ref,
        "bank_name": bank_name,
        "account_number": f"0000{account_ref[-4:]}",
        "mandate_id": f"mandate-{account_ref}",
        "mandate_status": "ready",
        "is_default": is_default,
    }


def _demand(
    *,
    task_id: str,
    amount: float,
    mode: str = "auto",
    preferred_account_id: str | None = None,
    explicit_sources: list[str] | None = None,
    explicit_split: dict[str, float] | None = None,
    use_dual_accounts: bool = False,
) -> TransferDemand:
    return TransferDemand(
        task_id=task_id,
        amount=amount,
        source_affinity=SourceAffinity(mode=mode),  # type: ignore[arg-type]
        preferred_account_id=preferred_account_id,
        explicit_sources=explicit_sources or [],
        explicit_split=explicit_split,
        use_dual_accounts=use_dual_accounts,
    )


@pytest.fixture
def sample_accounts() -> tuple[list[dict], dict[str, dict]]:
    access = _account("Access Bank", "acc_access", is_default=True)
    first = _account("First Bank", "acc_first")
    gtb = _account("GTBank", "acc_gtb")
    accounts = [access, first, gtb]
    return accounts, {"access": access, "first": first, "gtb": gtb}


@pytest.mark.asyncio
async def test_simple_batch_single_source_sufficient(sample_accounts) -> None:
    accounts, refs = sample_accounts
    provider = _MockDirectDebitProvider({"acc_access": 60000.0, "acc_first": 0.0, "acc_gtb": 0.0})
    coordinator = BatchFundingCoordinator(dd_provider=provider)
    demands = [
        _demand(
            task_id="t1",
            amount=20000.0,
            mode="explicit",
            preferred_account_id=refs["access"]["id"],
            explicit_sources=["Access Bank"],
        ),
        _demand(
            task_id="t2",
            amount=30000.0,
            mode="explicit",
            preferred_account_id=refs["access"]["id"],
            explicit_sources=["Access Bank"],
        ),
    ]

    result = await coordinator.coordinate(demands=demands, accounts=accounts, locale="en")

    assert result.is_feasible
    assert result.shortfalls is None
    assert result.plans_by_task["t1"].steps[0].bank_name == "Access Bank"
    assert result.plans_by_task["t2"].steps[0].bank_name == "Access Bank"


@pytest.mark.asyncio
async def test_simple_batch_single_source_insufficient(sample_accounts) -> None:
    accounts, refs = sample_accounts
    provider = _MockDirectDebitProvider({"acc_access": 50000.0, "acc_first": 0.0, "acc_gtb": 0.0})
    coordinator = BatchFundingCoordinator(dd_provider=provider)
    demands = [
        _demand(task_id="t1", amount=40000.0, mode="explicit", preferred_account_id=refs["access"]["id"]),
        _demand(task_id="t2", amount=30000.0, mode="explicit", preferred_account_id=refs["access"]["id"]),
    ]

    result = await coordinator.coordinate(demands=demands, accounts=accounts, locale="en")

    assert not result.is_feasible
    assert result.shortfalls is not None
    assert len(result.shortfalls) == 1
    assert result.shortfalls[0].task_id == "t2"


@pytest.mark.asyncio
async def test_auto_spillover(sample_accounts) -> None:
    accounts, refs = sample_accounts
    provider = _MockDirectDebitProvider({"acc_access": 50000.0, "acc_first": 50000.0, "acc_gtb": 0.0})
    coordinator = BatchFundingCoordinator(dd_provider=provider)
    demands = [
        _demand(task_id="t1", amount=40000.0, mode="auto", preferred_account_id=refs["access"]["id"]),
        _demand(task_id="t2", amount=30000.0, mode="auto", preferred_account_id=refs["access"]["id"]),
    ]

    result = await coordinator.coordinate(demands=demands, accounts=accounts, locale="en")

    assert result.is_feasible
    assert sum(step.amount for step in result.plans_by_task["t2"].steps) == pytest.approx(30000.0)


@pytest.mark.asyncio
async def test_explicit_source_honored(sample_accounts) -> None:
    accounts, refs = sample_accounts
    provider = _MockDirectDebitProvider({"acc_access": 50000.0, "acc_first": 50000.0, "acc_gtb": 0.0})
    coordinator = BatchFundingCoordinator(dd_provider=provider)
    demands = [
        _demand(
            task_id="t1",
            amount=40000.0,
            mode="explicit",
            preferred_account_id=refs["access"]["id"],
            explicit_sources=["Access Bank"],
        ),
        _demand(task_id="t2", amount=30000.0, mode="auto", preferred_account_id=refs["access"]["id"]),
    ]

    result = await coordinator.coordinate(demands=demands, accounts=accounts, locale="en")

    assert result.is_feasible
    assert result.plans_by_task["t1"].steps[0].bank_name == "Access Bank"


@pytest.mark.asyncio
async def test_explicit_source_short_no_silent_spill(sample_accounts) -> None:
    accounts, refs = sample_accounts
    provider = _MockDirectDebitProvider({"acc_access": 50000.0, "acc_first": 50000.0, "acc_gtb": 0.0})
    coordinator = BatchFundingCoordinator(dd_provider=provider)
    demands = [
        _demand(
            task_id="t1",
            amount=60000.0,
            mode="explicit",
            preferred_account_id=refs["access"]["id"],
            explicit_sources=["Access Bank"],
        )
    ]

    result = await coordinator.coordinate(demands=demands, accounts=accounts, locale="en")

    assert not result.is_feasible
    assert result.shortfalls is not None
    shortfall = result.shortfalls[0]
    assert shortfall.account_requested == "Access Bank"
    assert shortfall.alternate_accounts


@pytest.mark.asyncio
async def test_mixed_affinities_prioritize_larger_explicit_first(sample_accounts) -> None:
    accounts, refs = sample_accounts
    provider = _MockDirectDebitProvider({"acc_access": 50000.0, "acc_first": 50000.0, "acc_gtb": 0.0})
    coordinator = BatchFundingCoordinator(dd_provider=provider)
    demands = [
        _demand(task_id="t1", amount=10000.0, mode="explicit", preferred_account_id=refs["access"]["id"]),
        _demand(task_id="t2", amount=50000.0, mode="explicit", preferred_account_id=refs["access"]["id"]),
    ]

    result = await coordinator.coordinate(demands=demands, accounts=accounts, locale="en")

    assert not result.is_feasible
    assert result.shortfalls is not None
    assert result.shortfalls[0].task_id == "t1"


@pytest.mark.asyncio
async def test_explicit_pooling(sample_accounts) -> None:
    accounts, refs = sample_accounts
    provider = _MockDirectDebitProvider({"acc_access": 60000.0, "acc_first": 60000.0, "acc_gtb": 0.0})
    coordinator = BatchFundingCoordinator(dd_provider=provider)
    demands = [
        _demand(
            task_id="t1",
            amount=90000.0,
            mode="explicit",
            preferred_account_id=refs["access"]["id"],
            explicit_sources=["Access Bank", "First Bank"],
            use_dual_accounts=True,
        ),
        _demand(task_id="t2", amount=10000.0, mode="auto", preferred_account_id=refs["access"]["id"]),
    ]

    result = await coordinator.coordinate(demands=demands, accounts=accounts, locale="en")

    assert result.is_feasible
    assert len(result.plans_by_task["t1"].steps) == 2
    assert result.plans_by_task["t2"].is_sufficient


@pytest.mark.asyncio
async def test_explicit_split(sample_accounts) -> None:
    accounts, refs = sample_accounts
    provider = _MockDirectDebitProvider({"acc_access": 70000.0, "acc_first": 50000.0, "acc_gtb": 0.0})
    coordinator = BatchFundingCoordinator(dd_provider=provider)
    demands = [
        _demand(
            task_id="t1",
            amount=100000.0,
            mode="explicit",
            preferred_account_id=refs["access"]["id"],
            explicit_split={"Access Bank": 60000.0, "First Bank": 40000.0},
        ),
        _demand(task_id="t2", amount=10000.0, mode="auto", preferred_account_id=refs["access"]["id"]),
    ]

    result = await coordinator.coordinate(demands=demands, accounts=accounts, locale="en")

    assert result.is_feasible
    assert result.plans_by_task["t1"].explicit_split_applied
    assert {step.bank_name for step in result.plans_by_task["t1"].steps} == {"Access Bank", "First Bank"}


@pytest.mark.asyncio
async def test_total_infeasible(sample_accounts) -> None:
    accounts, refs = sample_accounts
    provider = _MockDirectDebitProvider({"acc_access": 50000.0, "acc_first": 50000.0, "acc_gtb": 0.0})
    coordinator = BatchFundingCoordinator(dd_provider=provider)
    demands = [
        _demand(task_id="t1", amount=80000.0, mode="auto", preferred_account_id=refs["access"]["id"]),
        _demand(task_id="t2", amount=50000.0, mode="auto", preferred_account_id=refs["access"]["id"]),
    ]

    result = await coordinator.coordinate(demands=demands, accounts=accounts, locale="en")

    assert not result.is_feasible
    assert result.total_demanded > result.total_available


@pytest.mark.asyncio
async def test_single_transfer_can_still_be_coordinated(sample_accounts) -> None:
    accounts, refs = sample_accounts
    provider = _MockDirectDebitProvider({"acc_access": 50000.0, "acc_first": 0.0, "acc_gtb": 0.0})
    coordinator = BatchFundingCoordinator(dd_provider=provider)
    demands = [_demand(task_id="t1", amount=20000.0, mode="auto", preferred_account_id=refs["access"]["id"])]

    result = await coordinator.coordinate(demands=demands, accounts=accounts, locale="en")

    assert result.is_feasible
    assert "t1" in result.plans_by_task
