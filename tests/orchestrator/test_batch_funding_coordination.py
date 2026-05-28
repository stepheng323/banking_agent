from uuid import uuid4

from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.domain import (
    TaskSpec,
    TaskStage,
    TransactionOutcome,
    TransactionResult,
)
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.node import advance_wave
from shared.clients.abstractions.direct_debit import BalanceResult


class _MockDirectDebitProvider:
    def __init__(self, balances: dict[str, float]) -> None:
        self.balances = balances

    async def get_balance(self, account_id: str, real_time: bool = True) -> BalanceResult:
        del real_time
        amount = float(self.balances.get(account_id, 0.0))
        return BalanceResult(success=True, available_balance=amount, ledger_balance=amount, currency="NGN")


class _TransferWorkerWithDD:
    def __init__(self, dd_provider: _MockDirectDebitProvider) -> None:
        self.dd_provider = dd_provider
        self.calls: list[dict] = []

    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del context, user_message, pin_verified
        self.calls.append(payload.copy())
        return TransactionResult(outcome=TransactionOutcome.OK, patch={})


class _TransferWorkerNoDD:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del context, user_message, pin_verified
        self.calls.append(payload.copy())
        return TransactionResult(outcome=TransactionOutcome.OK, patch={})


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


async def test_batch_funding_injects_plans_before_transfer_worker() -> None:
    access = _account("Access Bank", "acc_access", is_default=True)
    first = _account("First Bank", "acc_first")
    provider = _MockDirectDebitProvider({"acc_access": 80000.0, "acc_first": 20000.0})
    worker = _TransferWorkerWithDD(provider)

    state = OrchestratorState(
        user_id="u_batch_1",
        phone_number="2348000001001",
        channel="whatsapp",
        waves=[["t1", "t2"]],
        current_wave_index=0,
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "amount": 30000.0,
                    "source_account_id": access["id"],
                    "source_bank_name": "Access Bank",
                    "source_affinity_mode": "auto",
                },
            ),
            "t2": TaskSpec(
                id="t2",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "amount": 30000.0,
                    "source_account_id": access["id"],
                    "source_bank_name": "Access Bank",
                    "source_affinity_mode": "auto",
                },
            ),
        },
        loaded_context={"language": "en", "accounts": [access, first]},
    )
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)

    assert "pending_interrupt" not in updates
    assert len(worker.calls) == 2
    for payload in worker.calls:
        plan = payload.get("funding_plan")
        assert isinstance(plan, dict)
        assert "planned_for_amount" in plan
        assert "planned_for_source_account_id" in plan
        assert "planned_for_source_accounts" in plan
        assert "planned_for_use_dual_accounts" in plan
        assert "planned_for_explicit_split" in plan


async def test_batch_funding_infeasible_blocks_wave_with_input_interrupt() -> None:
    access = _account("Access Bank", "acc_access", is_default=True)
    first = _account("First Bank", "acc_first")
    provider = _MockDirectDebitProvider({"acc_access": 50000.0, "acc_first": 0.0})
    worker = _TransferWorkerWithDD(provider)

    state = OrchestratorState(
        user_id="u_batch_2",
        phone_number="2348000001002",
        channel="whatsapp",
        waves=[["t1", "t2"]],
        current_wave_index=0,
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "amount": 40000.0,
                    "source_account_id": access["id"],
                    "source_bank_name": "Access Bank",
                    "source_affinity_mode": "explicit",
                },
            ),
            "t2": TaskSpec(
                id="t2",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "amount": 30000.0,
                    "source_account_id": access["id"],
                    "source_bank_name": "Access Bank",
                    "source_affinity_mode": "explicit",
                },
            ),
        },
        loaded_context={"language": "en", "accounts": [access, first]},
    )
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)

    assert updates["pending_interrupt"].kind == "input"
    assert updates["outbox"][0]["type"] == "say"
    assert "Insufficient funds for this batch" in updates["outbox"][0]["text"]
    assert worker.calls == []


async def test_batch_funding_skips_when_transfer_worker_has_no_dd_provider() -> None:
    access = _account("Access Bank", "acc_access", is_default=True)
    first = _account("First Bank", "acc_first")
    worker = _TransferWorkerNoDD()

    state = OrchestratorState(
        user_id="u_batch_3",
        phone_number="2348000001003",
        channel="whatsapp",
        waves=[["t1", "t2"]],
        current_wave_index=0,
        tasks={
            "t1": TaskSpec(
                id="t1",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "amount": 30000.0,
                    "source_account_id": access["id"],
                    "source_bank_name": "Access Bank",
                    "source_affinity_mode": "auto",
                },
            ),
            "t2": TaskSpec(
                id="t2",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "amount": 30000.0,
                    "source_account_id": access["id"],
                    "source_bank_name": "Access Bank",
                    "source_affinity_mode": "auto",
                },
            ),
        },
        loaded_context={"language": "en", "accounts": [access, first]},
    )
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)

    assert "pending_interrupt" not in updates
    assert len(worker.calls) == 2
    assert worker.calls[0].get("funding_plan") is None
    assert worker.calls[1].get("funding_plan") is None
