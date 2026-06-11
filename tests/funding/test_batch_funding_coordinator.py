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
    source_pooling_locked: bool = False,
) -> TransferDemand:
    return TransferDemand(
        task_id=task_id,
        amount=amount,
        source_affinity=SourceAffinity(mode=mode),  # type: ignore[arg-type]
        preferred_account_id=preferred_account_id,
        explicit_sources=explicit_sources or [],
        explicit_split=explicit_split,
        use_dual_accounts=use_dual_accounts,
        source_pooling_locked=source_pooling_locked,
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

    assert not result.is_feasible
    assert result.requires_user_approval
    assert sum(step.amount for step in result.suggested_plans_by_task["t2"].steps) == pytest.approx(30000.0)


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

    assert not result.is_feasible
    assert result.requires_user_approval
    assert result.suggested_plans_by_task["t1"].steps[0].bank_name == "Access Bank"


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
async def test_batch_selected_source_shortfall_prompts_for_one_more_source(sample_accounts) -> None:
    accounts, refs = sample_accounts
    provider = _MockDirectDebitProvider({"acc_access": 50000.0, "acc_first": 50000.0, "acc_gtb": 0.0})
    coordinator = BatchFundingCoordinator(dd_provider=provider)
    demands = [
        _demand(task_id="t1", amount=10000.0, mode="explicit", preferred_account_id=refs["access"]["id"]),
        _demand(task_id="t2", amount=50000.0, mode="explicit", preferred_account_id=refs["access"]["id"]),
    ]

    result = await coordinator.coordinate(demands=demands, accounts=accounts, locale="en")

    assert not result.is_feasible
    assert result.requires_user_approval
    assert result.shortfalls is None
    assert set(result.suggested_plans_by_task) == {"t1", "t2"}
    assert result.suggested_source_ids == [refs["access"]["id"], refs["first"]["id"]]
    assert "Funding breakdown" in (result.suggestion or "")
    assert "Access Bank" in (result.suggestion or "")
    assert "First Bank" in (result.suggestion or "")


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

    assert not result.is_feasible
    assert result.requires_user_approval
    assert len(result.suggested_plans_by_task["t1"].steps) == 2
    assert result.suggested_plans_by_task["t2"].is_sufficient


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
async def test_batch_auto_funding_blocks_when_three_sources_would_be_needed(sample_accounts) -> None:
    accounts, refs = sample_accounts
    provider = _MockDirectDebitProvider({"acc_access": 30000.0, "acc_first": 30000.0, "acc_gtb": 30000.0})
    coordinator = BatchFundingCoordinator(dd_provider=provider)
    demands = [
        _demand(task_id="t1", amount=40000.0, mode="auto", preferred_account_id=refs["access"]["id"]),
        _demand(task_id="t2", amount=30000.0, mode="auto", preferred_account_id=refs["access"]["id"]),
    ]

    result = await coordinator.coordinate(demands=demands, accounts=accounts, locale="en")

    assert not result.is_feasible
    assert not result.requires_user_approval
    assert result.shortfalls is not None
    assert "at most 2 accounts" in (result.suggestion or "")
    assert "Your default Access Bank has ₦30,000" in (result.suggestion or "")
    assert "Access Bank + First Bank:" not in (result.suggestion or "")
    assert "With that limit, the most I can cover is ₦60,000" in (result.suggestion or "")
    assert "Available sources:" in (result.suggestion or "")
    assert "Access Bank (···0000) (default): ₦30,000" in (result.suggestion or "")
    assert "First Bank (···0000): ₦30,000" in (result.suggestion or "")
    assert "GTBank (···0000): ₦30,000" in (result.suggestion or "")
    assert "₦60,000" in (result.suggestion or "")
    assert "₦10,000" in (result.suggestion or "")
    assert result.anchor_source_ids == [refs["access"]["id"]]
    assert float(result.capped_available) == pytest.approx(60000.0)


@pytest.mark.asyncio
async def test_batch_auto_pooled_funding_requires_user_approval(sample_accounts) -> None:
    accounts, refs = sample_accounts
    provider = _MockDirectDebitProvider({"acc_access": 30000.0, "acc_first": 50000.0, "acc_gtb": 0.0})
    coordinator = BatchFundingCoordinator(dd_provider=provider)
    demands = [
        _demand(task_id="t1", amount=40000.0, mode="auto", preferred_account_id=refs["access"]["id"]),
        _demand(task_id="t2", amount=10000.0, mode="auto", preferred_account_id=refs["access"]["id"]),
    ]

    result = await coordinator.coordinate(demands=demands, accounts=accounts, locale="en")

    assert not result.is_feasible
    assert result.requires_user_approval
    assert result.shortfalls is None
    assert set(result.suggested_plans_by_task) == {"t1", "t2"}
    assert result.plans_by_task == {}
    assert "Funding breakdown" in (result.suggestion or "")
    assert "Access Bank" in (result.suggestion or "")
    assert "First Bank" in (result.suggestion or "")
    assert "Available sources:" not in (result.suggestion or "")
    assert "Total debit: ₦50,000" in (result.suggestion or "")
    assert "Reply confirm to authorize these transfers" in (result.suggestion or "")
    assert result.anchor_source_ids == [refs["access"]["id"]]
    assert result.suggested_source_ids == [refs["access"]["id"], refs["first"]["id"]]


@pytest.mark.asyncio
async def test_batch_selected_source_shortfall_names_selected_anchor(sample_accounts) -> None:
    accounts, refs = sample_accounts
    provider = _MockDirectDebitProvider({"acc_access": 50000.0, "acc_first": 50000.0, "acc_gtb": 30000.0})
    coordinator = BatchFundingCoordinator(dd_provider=provider)
    demands = [
        _demand(
            task_id="t1",
            amount=60000.0,
            mode="explicit",
            preferred_account_id=refs["gtb"]["id"],
            explicit_sources=["GTBank"],
        )
    ]

    result = await coordinator.coordinate(demands=demands, accounts=accounts, locale="en")

    assert not result.is_feasible
    assert result.shortfalls is not None
    assert result.anchor_source_ids == [refs["gtb"]["id"]]
    assert result.source_options[0].bank_name == "GTBank"
    assert result.source_options[0].is_selected
    assert "Your selected GTBank has ₦30,000" in (result.suggestion or "")
    assert "Available sources:" in (result.suggestion or "")
    assert "Access Bank (···0000) (default): ₦50,000" in (result.suggestion or "")


@pytest.mark.asyncio
async def test_batch_auto_pooled_funding_suggestion_uses_last4_when_full_account_number_is_absent() -> None:
    access = _account("Access Bank", "acc_access", is_default=True)
    gtb = _account("GTBank", "acc_gtb")
    access.pop("account_number")
    gtb.pop("account_number")
    access["account_number_last4"] = "0003"
    gtb["last4"] = "0002"
    provider = _MockDirectDebitProvider({"acc_access": 30000.0, "acc_gtb": 30000.0})
    coordinator = BatchFundingCoordinator(dd_provider=provider)
    demands = [
        _demand(task_id="t1", amount=30000.0, mode="auto", preferred_account_id=access["id"]),
        _demand(task_id="t2", amount=30000.0, mode="auto", preferred_account_id=access["id"]),
    ]

    result = await coordinator.coordinate(demands=demands, accounts=[access, gtb], locale="en")

    assert not result.is_feasible
    assert result.requires_user_approval
    assert "Access Bank (···0003): ₦30,000" in (result.suggestion or "")
    assert "GTBank (···0002): ₦30,000" in (result.suggestion or "")
    assert "????" not in (result.suggestion or "")


@pytest.mark.asyncio
async def test_batch_explicit_pooled_funding_does_not_require_extra_approval(sample_accounts) -> None:
    accounts, refs = sample_accounts
    provider = _MockDirectDebitProvider({"acc_access": 30000.0, "acc_first": 50000.0, "acc_gtb": 0.0})
    coordinator = BatchFundingCoordinator(dd_provider=provider)
    demands = [
        _demand(
            task_id="t1",
            amount=40000.0,
            mode="explicit",
            preferred_account_id=refs["access"]["id"],
            explicit_sources=["Access Bank", "First Bank"],
            use_dual_accounts=True,
        ),
    ]

    result = await coordinator.coordinate(demands=demands, accounts=accounts, locale="en")

    assert result.is_feasible
    assert not result.requires_user_approval
    assert "t1" in result.plans_by_task


@pytest.mark.asyncio
async def test_batch_explicit_anchor_plus_gtbank_sources_cover_two_transfer_batch(sample_accounts) -> None:
    accounts, refs = sample_accounts
    provider = _MockDirectDebitProvider({"acc_access": 30000.0, "acc_first": 30000.0, "acc_gtb": 30000.0})
    coordinator = BatchFundingCoordinator(dd_provider=provider)
    demands = [
        _demand(
            task_id="t1",
            amount=30000.0,
            mode="explicit",
            preferred_account_id=refs["access"]["id"],
            explicit_sources=["Access Bank", "GTBank"],
            use_dual_accounts=True,
        ),
        _demand(
            task_id="t2",
            amount=30000.0,
            mode="explicit",
            preferred_account_id=refs["access"]["id"],
            explicit_sources=["Access Bank", "GTBank"],
            use_dual_accounts=True,
        ),
    ]

    result = await coordinator.coordinate(demands=demands, accounts=accounts, locale="en")

    assert result.is_feasible
    assert not result.requires_user_approval
    assert {step.bank_name for plan in result.plans_by_task.values() for step in plan.steps} == {
        "Access Bank",
        "GTBank",
    }


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


@pytest.mark.asyncio
async def test_batch_auto_pooled_funding_asks_when_multiple_accounts_can_cover_remaining(sample_accounts) -> None:
    accounts, refs = sample_accounts
    provider = _MockDirectDebitProvider({"acc_access": 30000.0, "acc_first": 30000.0, "acc_gtb": 30000.0})
    coordinator = BatchFundingCoordinator(dd_provider=provider)
    demands = [
        _demand(task_id="t1", amount=30000.0, mode="auto", preferred_account_id=refs["access"]["id"]),
        _demand(task_id="t2", amount=30000.0, mode="auto", preferred_account_id=refs["access"]["id"]),
    ]

    result = await coordinator.coordinate(demands=demands, accounts=accounts, locale="en")

    assert not result.is_feasible
    assert result.requires_source_choice
    assert not result.requires_user_approval
    assert result.suggested_plans_by_task == {}
    assert result.source_choice is not None
    assert set(result.source_choice.candidate_source_ids) == {refs["first"]["id"], refs["gtb"]["id"]}
    assert "Total needed: ₦60,000" in (result.suggestion or "")
    assert "To cover the remaining ₦30,000, choose one more source" in (result.suggestion or "")
    assert "• First Bank" in (result.suggestion or "")
    assert "• GTBank" in (result.suggestion or "")
    assert "Reply with the bank you want to use." in (result.suggestion or "")
    assert "Which account should I use?" not in (result.suggestion or "")


@pytest.mark.asyncio
async def test_batch_selected_first_bank_reduction_asks_for_one_more_source(sample_accounts) -> None:
    accounts, refs = sample_accounts
    provider = _MockDirectDebitProvider({"acc_access": 30000.0, "acc_first": 30000.0, "acc_gtb": 30000.0})
    coordinator = BatchFundingCoordinator(dd_provider=provider)
    demands = [
        _demand(
            task_id="t1",
            amount=30000.0,
            mode="explicit",
            preferred_account_id=refs["first"]["id"],
            explicit_sources=["First Bank"],
        ),
        _demand(
            task_id="t2",
            amount=30000.0,
            mode="explicit",
            preferred_account_id=refs["first"]["id"],
            explicit_sources=["First Bank"],
        ),
    ]

    result = await coordinator.coordinate(demands=demands, accounts=accounts, locale="en")

    assert not result.is_feasible
    assert result.requires_source_choice
    assert not result.requires_user_approval
    assert result.source_choice is not None
    assert result.anchor_source_ids == [refs["first"]["id"]]
    assert set(result.source_choice.candidate_source_ids) == {refs["access"]["id"], refs["gtb"]["id"]}
    assert "Total needed: ₦60,000" in (result.suggestion or "")
    assert "Your selected First Bank has ₦30,000" in (result.suggestion or "")
    assert "To cover the remaining ₦30,000, choose one more source" in (result.suggestion or "")
    assert "only ₦90,000" not in (result.suggestion or "")


@pytest.mark.asyncio
async def test_batch_only_gtb_keeps_single_source_shortfall(sample_accounts) -> None:
    accounts, refs = sample_accounts
    provider = _MockDirectDebitProvider({"acc_access": 30000.0, "acc_first": 30000.0, "acc_gtb": 30000.0})
    coordinator = BatchFundingCoordinator(dd_provider=provider)
    demands = [
        _demand(
            task_id="t1",
            amount=30000.0,
            mode="explicit",
            preferred_account_id=refs["gtb"]["id"],
            explicit_sources=["GTBank"],
            source_pooling_locked=True,
        ),
        _demand(
            task_id="t2",
            amount=30000.0,
            mode="explicit",
            preferred_account_id=refs["gtb"]["id"],
            explicit_sources=["GTBank"],
            source_pooling_locked=True,
        ),
    ]

    result = await coordinator.coordinate(demands=demands, accounts=accounts, locale="en")

    assert not result.is_feasible
    assert not result.requires_source_choice
    assert not result.requires_user_approval
    assert result.shortfalls is not None
    assert "GTBank" in (result.suggestion or "")


@pytest.mark.asyncio
async def test_batch_auto_pooled_funding_says_when_only_one_account_can_cover_remaining(sample_accounts) -> None:
    accounts, refs = sample_accounts
    provider = _MockDirectDebitProvider({"acc_access": 30000.0, "acc_first": 10000.0, "acc_gtb": 30000.0})
    coordinator = BatchFundingCoordinator(dd_provider=provider)
    demands = [
        _demand(task_id="t1", amount=30000.0, mode="auto", preferred_account_id=refs["access"]["id"]),
        _demand(task_id="t2", amount=30000.0, mode="auto", preferred_account_id=refs["access"]["id"]),
    ]

    result = await coordinator.coordinate(demands=demands, accounts=accounts, locale="en")

    assert not result.is_feasible
    assert result.requires_user_approval
    assert not result.requires_source_choice
    assert result.suggested_source_ids == [refs["access"]["id"], refs["gtb"]["id"]]
    assert "Only GTBank can cover the remaining ₦30,000" in (result.suggestion or "")
    assert "Funding breakdown" in (result.suggestion or "")
    assert "GTBank" in (result.suggestion or "")
