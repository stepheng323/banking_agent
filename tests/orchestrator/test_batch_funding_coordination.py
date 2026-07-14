from uuid import uuid4

import pytest
from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.domain import PendingInterrupt, TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.node import advance_wave
from apps.chat.src.agent.orchestrator.workflows.interrupt.deterministic.runner_deterministic_input import (
    _input_shortcut_updates,
)
from apps.chat.src.agent.orchestrator.workflows.interrupt.runtime import build_interrupt_runtime
from banking.runtime.results import TransactionOutcome, TransactionResult
from shared.clients.abstractions.direct_debit import BalanceResult
from tests.orchestrator.routing_fixtures import execution_test_directive


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


class _TransferNeedsConfirmationWithDD:
    def __init__(self, dd_provider: _MockDirectDebitProvider, source_account: dict) -> None:
        self.dd_provider = dd_provider
        self.source_account = source_account
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
        amount = payload.get("amount")
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_CONFIRMATION,
            patch={
                "source_account_id": self.source_account["id"],
                "source_bank_name": self.source_account["bank_name"],
                "source_account_number": self.source_account["account_number"],
                "funding_plan": {
                    "is_single_source": False,
                    "old_single_transfer_plan": True,
                    "steps": [
                        {
                            "account_id": self.source_account["id"],
                            "bank_name": self.source_account["bank_name"],
                            "amount": 30000,
                        },
                    ],
                },
            },
            confirmation_summary=f"Confirm one transfer for {amount}",
            confirmation_snapshot={"amount": amount},
        )


class _TransferNeedsConfirmationNoFundingPatch:
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
        amount = payload.get("amount")
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_CONFIRMATION,
            patch={},
            confirmation_summary=f"Confirm one transfer for {amount}",
            confirmation_snapshot={"amount": amount},
        )


class _TransferResolvesRecipientThenNeedsConfirmation:
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
        amount = payload.get("amount")
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_CONFIRMATION,
            patch={
                "recipient_resolved_name": "Pastor Bright",
                "recipient_resolution_provider": "mock",
            },
            confirmation_summary=f"Confirm one transfer for {amount}",
            confirmation_snapshot={"amount": amount},
        )


class _TransferNeedsRecipientDetailsWithDD:
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
        if not (payload.get("recipient_account") and payload.get("recipient_bank_name")):
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["recipient_account", "recipient_bank_name"],
                prompt="Please share the account number and bank.",
            )
        return TransactionResult(outcome=TransactionOutcome.OK, patch={})


class _TransferSingleLegFundingAdjustmentWithDD:
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
        if not (payload.get("recipient_account") and payload.get("recipient_bank_name")):
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["recipient_account", "recipient_bank_name"],
                prompt="Please share the account number and bank.",
            )
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=["suggested_funding_plan", "amount", "source_accounts", "explicit_split"],
            prompt="Funding review\n\nThis transfer needs ₦40,000.",
            details={"review_state": "funding_adjustment", "funding_plan_status": "suggested_pooling"},
            patch={
                "suggested_funding_plan": {
                    "is_sufficient": True,
                    "steps": [
                        {
                            "account_id": "acc-access",
                            "account_number": "00030003",
                            "bank_name": "Access Bank",
                            "amount": "30000.00",
                        }
                    ],
                },
                "funding_plan": None,
            },
        )


class _TransferResolvesByAliasThenNeedsConfirmation:
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
        recipient_name = str(payload.get("recipient_name") or "").strip().casefold()
        resolved_name = "Yusuf Ibrahim" if recipient_name == "ay" else "Fatima Zahra Musa"
        amount = payload.get("amount")
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_CONFIRMATION,
            patch={
                "recipient_resolved_name": resolved_name,
                "recipient_resolution_provider": "mock",
            },
            confirmation_summary=f"Confirm one transfer for {amount}",
            confirmation_snapshot={"amount": amount},
        )


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


def _resolved_recipient(name: str) -> dict:
    return {"recipient_resolved_name": name, "recipient_resolution_provider": "mock"}


async def test_batch_funding_injects_plans_before_transfer_worker() -> None:
    access = _account("Access Bank", "acc_access", is_default=True)
    first = _account("First Bank", "acc_first")
    provider = _MockDirectDebitProvider({"acc_access": 80000.0, "acc_first": 20000.0})
    worker = _TransferWorkerWithDD(provider)

    state = OrchestratorState(
        turn_directive=execution_test_directive(),
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
                    "recipient_account": "8067892221",
                    "recipient_bank_name": "Wema",
                    **_resolved_recipient("Pastor Bright"),
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
                    "recipient_account": "8080844362",
                    "recipient_bank_name": "Opay",
                    **_resolved_recipient("Yusuf Ibrahim"),
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
        turn_directive=execution_test_directive(),
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
                    "recipient_account": "8067892221",
                    "recipient_bank_name": "Wema",
                    **_resolved_recipient("Pastor Bright"),
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
                    "recipient_account": "8080844362",
                    "recipient_bank_name": "Opay",
                    **_resolved_recipient("Yusuf Ibrahim"),
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
        turn_directive=execution_test_directive(),
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
                    "recipient_account": "8067892221",
                    "recipient_bank_name": "Wema",
                    **_resolved_recipient("Pastor Bright"),
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
                    "recipient_account": "8080844362",
                    "recipient_bank_name": "Opay",
                    **_resolved_recipient("Yusuf Ibrahim"),
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


async def test_batch_funding_shortfall_enters_funding_adjustment_before_confirmation() -> None:
    access = _account("Access Bank", "acc_access", is_default=True)
    gtb = _account("GTBank", "acc_gtb")
    provider = _MockDirectDebitProvider({"acc_access": 30000.0, "acc_gtb": 20000.0})
    worker = _TransferWorkerWithDD(provider)

    state = OrchestratorState(
        turn_directive=execution_test_directive(),
        user_id="u_batch_shortfall",
        phone_number="2348000001006",
        channel="whatsapp",
        waves=[["t_mom", "t_ay"]],
        current_wave_index=0,
        tasks={
            "t_mom": TaskSpec(
                id="t_mom",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "amount": 40000.0,
                    "recipient_name": "mom",
                    "recipient_account": "8067892221",
                    "recipient_bank_name": "Wema",
                    **_resolved_recipient("Pastor Bright"),
                    "source_account_id": access["id"],
                    "source_affinity_mode": "auto",
                },
            ),
            "t_ay": TaskSpec(
                id="t_ay",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "amount": 30000.0,
                    "recipient_name": "ay",
                    "recipient_account": "8080844362",
                    "recipient_bank_name": "Opay",
                    **_resolved_recipient("Yusuf Ibrahim"),
                    "source_account_id": access["id"],
                    "source_affinity_mode": "auto",
                },
            ),
        },
        loaded_context={"language": "en", "accounts": [access, gtb]},
    )
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)

    assert worker.calls == []
    interrupt = updates["pending_interrupt"]
    assert interrupt.kind == "input"
    assert set(interrupt.task_ids) == {"t_mom", "t_ay"}
    assert updates["tasks"]["t_mom"].stage == TaskStage.AWAITING_FUNDING_ADJUSTMENT
    assert updates["tasks"]["t_ay"].stage == TaskStage.AWAITING_FUNDING_ADJUSTMENT


async def test_batch_funding_waits_for_recipient_details_before_review() -> None:
    access = _account("Access Bank", "acc_access", is_default=True)
    gtb = _account("GTBank", "acc_gtb")
    provider = _MockDirectDebitProvider({"acc_access": 30000.0, "acc_gtb": 30000.0})
    worker = _TransferNeedsRecipientDetailsWithDD(provider)

    state = OrchestratorState(
        turn_directive=execution_test_directive(),
        user_id="u_batch_missing_recipients",
        phone_number="2348000001011",
        channel="whatsapp",
        waves=[["t_mom", "t_ay"]],
        current_wave_index=0,
        tasks={
            "t_mom": TaskSpec(
                id="t_mom",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"amount": 40000.0, "recipient_name": "mom", "source_affinity_mode": "auto"},
            ),
            "t_ay": TaskSpec(
                id="t_ay",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"amount": 30000.0, "recipient_name": "ay", "source_affinity_mode": "auto"},
            ),
        },
        loaded_context={"language": "en", "accounts": [access, gtb]},
    )
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)

    assert worker.calls
    assert updates["pending_interrupt"].kind == "input"
    assert "Funding review" not in updates["outbox"][-1]["text"]
    assert "account number and bank" in updates["outbox"][-1]["text"]


async def test_batch_suppresses_single_leg_funding_until_all_recipients_are_ready() -> None:
    access = _account("Access Bank", "acc_access", is_default=True)
    gtb = _account("GTBank", "acc_gtb")
    provider = _MockDirectDebitProvider({"acc_access": 30000.0, "acc_gtb": 30000.0})
    worker = _TransferSingleLegFundingAdjustmentWithDD(provider)

    state = OrchestratorState(
        turn_directive=execution_test_directive(),
        user_id="u_batch_partial_recipient_readiness",
        phone_number="2348000001014",
        channel="whatsapp",
        waves=[["t_mom", "t_ay"]],
        current_wave_index=0,
        tasks={
            "t_mom": TaskSpec(
                id="t_mom",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "amount": 40000.0,
                    "recipient_name": "mom",
                    "recipient_account": "8067892221",
                    "recipient_bank_name": "Wema",
                    **_resolved_recipient("Pastor Bright"),
                    "source_affinity_mode": "auto",
                },
            ),
            "t_ay": TaskSpec(
                id="t_ay",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "amount": 30000.0,
                    "recipient_name": "ay",
                    "source_affinity_mode": "auto",
                },
            ),
        },
        loaded_context={"language": "en", "accounts": [access, gtb]},
    )
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)

    assert len(worker.calls) == 2
    interrupt = updates["pending_interrupt"]
    assert interrupt.kind == "input"
    assert interrupt.task_ids == ["t_ay"]
    assert "Funding review" not in updates["outbox"][-1]["text"]
    assert "account number and bank" in updates["outbox"][-1]["text"]
    mom_payload = updates["tasks"]["t_mom"].payload
    assert mom_payload.get("funding_plan") is None
    assert mom_payload.get("suggested_funding_plan") is None


async def test_batch_recipient_review_waits_until_new_sibling_details_are_resolved() -> None:
    access = _account("Access Bank", "acc_access", is_default=True)
    gtb = _account("GTBank", "acc_gtb")
    provider = _MockDirectDebitProvider({"acc_access": 30000.0, "acc_gtb": 30000.0})
    worker = _TransferResolvesByAliasThenNeedsConfirmation(provider)

    state = OrchestratorState(
        turn_directive=execution_test_directive(),
        user_id="u_batch_recipient_review_waits",
        phone_number="2348000001015",
        channel="whatsapp",
        waves=[["t_mom", "t_ay"]],
        current_wave_index=0,
        tasks={
            "t_mom": TaskSpec(
                id="t_mom",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "amount": 40000.0,
                    "recipient_name": "mom",
                    "recipient_account": "8067892221",
                    "recipient_bank_name": "Wema",
                    "recipient_resolved_name": "Fatima Zahra Musa",
                    "recipient_resolution_provider": "mock",
                    "recipient_review_required": True,
                    "source_affinity_mode": "auto",
                },
            ),
            "t_ay": TaskSpec(
                id="t_ay",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "amount": 30000.0,
                    "recipient_name": "ay",
                    "recipient_account": "8937783748",
                    "recipient_bank_name": "Opay",
                    "source_affinity_mode": "auto",
                },
            ),
        },
        loaded_context={"language": "en", "accounts": [access, gtb]},
    )
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)

    assert len(worker.calls) == 2
    interrupt = updates["pending_interrupt"]
    assert interrupt.kind == "input"
    assert interrupt.fields_by_task == {
        "t_mom": ["recipient_review_confirmed"],
        "t_ay": ["recipient_review_confirmed"],
    }
    prompt = updates["outbox"][0]["text"]
    assert "Recipient review" in prompt
    assert "mom → Fatima Zahra Musa" in prompt
    assert "Wema • ****2221" in prompt
    assert "ay → Yusuf Ibrahim" in prompt
    assert "Opay • ****3748" in prompt


async def test_batch_funding_waits_for_recipient_resolution_after_destination_details() -> None:
    access = _account("Access Bank", "acc_access", is_default=True)
    gtb = _account("GTBank", "acc_gtb")
    provider = _MockDirectDebitProvider({"acc_access": 30000.0, "acc_gtb": 30000.0})
    worker = _TransferNeedsRecipientDetailsWithDD(provider)

    state = OrchestratorState(
        turn_directive=execution_test_directive(),
        user_id="u_batch_unresolved_recipients",
        phone_number="2348000001013",
        channel="whatsapp",
        waves=[["t_mom", "t_ay"]],
        current_wave_index=0,
        tasks={
            "t_mom": TaskSpec(
                id="t_mom",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "amount": 30000.0,
                    "recipient_name": "mom",
                    "recipient_account": "8067892221",
                    "recipient_bank_name": "Wema",
                    **_resolved_recipient("Pastor Bright"),
                    "source_affinity_mode": "auto",
                },
            ),
            "t_ay": TaskSpec(
                id="t_ay",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "amount": 30000.0,
                    "recipient_name": "ay",
                    "recipient_account": "8080844362",
                    "recipient_bank_name": "Opay",
                    "source_affinity_mode": "auto",
                },
            ),
        },
        loaded_context={"language": "en", "accounts": [access, gtb]},
    )
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)

    assert len(worker.calls) == 2
    assert "pending_interrupt" not in updates


async def test_batch_auto_pooled_funding_prompts_for_approval_before_worker() -> None:
    access = _account("Access Bank", "acc_access", is_default=True)
    first = _account("First Bank", "acc_first")
    provider = _MockDirectDebitProvider({"acc_access": 30000.0, "acc_first": 50000.0})
    worker = _TransferWorkerWithDD(provider)

    state = OrchestratorState(
        turn_directive=execution_test_directive(),
        user_id="u_batch_suggestion",
        phone_number="2348000001007",
        channel="whatsapp",
        waves=[["t_mom", "t_ay"]],
        current_wave_index=0,
        tasks={
            "t_mom": TaskSpec(
                id="t_mom",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "amount": 40000.0,
                    "recipient_name": "mom",
                    "recipient_account": "8067892221",
                    "recipient_bank_name": "Wema",
                    **_resolved_recipient("Pastor Bright"),
                    "source_account_id": access["id"],
                    "source_affinity_mode": "auto",
                },
            ),
            "t_ay": TaskSpec(
                id="t_ay",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "amount": 10000.0,
                    "recipient_name": "ay",
                    "recipient_account": "8080844362",
                    "recipient_bank_name": "Opay",
                    **_resolved_recipient("Yusuf Ibrahim"),
                    "source_account_id": access["id"],
                    "source_affinity_mode": "auto",
                },
            ),
        },
        loaded_context={"language": "en", "accounts": [access, first]},
    )
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)

    assert worker.calls == []
    interrupt = updates["pending_interrupt"]
    assert interrupt.kind == "input"
    assert set(interrupt.fields_by_task["t_mom"]) == {
        "suggested_funding_plan",
        "amount",
        "source_accounts",
        "explicit_split",
    }
    assert updates["tasks"]["t_mom"].payload["funding_plan"] is None
    assert isinstance(updates["tasks"]["t_mom"].payload["suggested_funding_plan"], dict)
    assert updates["tasks"]["t_ay"].payload["funding_plan"] is None
    assert isinstance(updates["tasks"]["t_ay"].payload["suggested_funding_plan"], dict)
    prompt = updates["outbox"][-1]["text"]
    assert "This batch needs ₦50,000, but your Access Bank only has ₦30,000" in prompt
    assert "You can pool from an additional account to cover the remaining ₦20,000" in prompt
    assert "First Bank (₦50,000 available)" in prompt
    assert "Reply with the number or bank name" in prompt
    assert "????" not in prompt
    assert "Available sources:" not in prompt
    assert "Confirm Transfers" not in prompt


@pytest.mark.asyncio
async def test_accepting_suggested_batch_funding_promotes_plan() -> None:
    suggested_plan = {
        "transfer_amount": "40000.00",
        "total_funded": "40000.00",
        "is_sufficient": True,
        "is_single_source": False,
        "planned_for_amount": "40000.00",
        "steps": [
            {"account_id": "acc-1", "account_number": "12340003", "bank_name": "Access Bank", "amount": "40000.00"}
        ],
    }
    state = OrchestratorState(
        turn_directive=execution_test_directive(),
        user_id="u_batch_accept_suggestion",
        phone_number="2348000001008",
        channel="whatsapp",
        last_message_text="yes",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t_mom"],
            fields_by_task={
                "t_mom": ["suggested_funding_plan", "amount", "source_accounts", "explicit_split"],
            },
            prompt="Reply yes to use this breakdown.",
        ),
        tasks={
            "t_mom": TaskSpec(
                id="t_mom",
                type="transfer",
                stage=TaskStage.AWAITING_FUNDING_ADJUSTMENT,
                payload={
                    "amount": 40000.0,
                    "recipient_name": "mom",
                    "recipient_account": "8067892221",
                    "recipient_bank_name": "Wema",
                    "source_account_id": "acc-1",
                    "suggested_funding_plan": suggested_plan,
                    "funding_plan": None,
                },
            )
        },
        loaded_context={"language": "en", "accounts": []},
    )
    config: RunnableConfig = {"configurable": {"services": {}}, "recursion_limit": 50}
    runtime = build_interrupt_runtime(state=state, config=config)

    updates = await _input_shortcut_updates(state=state, runtime=runtime)

    assert updates is not None
    assert updates["pending_interrupt"] is None
    task = updates["tasks"]["t_mom"]
    assert task.stage == TaskStage.EXTRACTED
    assert task.payload["funding_plan"] == suggested_plan
    assert task.payload["suggested_funding_plan"] is None


@pytest.mark.asyncio
async def test_accepting_suggested_batch_funding_rejects_missing_recipient_destination() -> None:
    suggested_plan = {
        "transfer_amount": "40000.00",
        "total_funded": "40000.00",
        "is_sufficient": True,
        "is_single_source": False,
        "planned_for_amount": "40000.00",
        "steps": [
            {"account_id": "acc-1", "account_number": "12340003", "bank_name": "Access Bank", "amount": "40000.00"}
        ],
    }
    state = OrchestratorState(
        turn_directive=execution_test_directive(),
        user_id="u_batch_reject_stale_suggestion",
        phone_number="2348000001012",
        channel="whatsapp",
        last_message_text="yes",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t_mom"],
            fields_by_task={
                "t_mom": ["suggested_funding_plan", "amount", "source_accounts", "explicit_split"],
            },
            prompt="Reply yes to use this breakdown.",
        ),
        tasks={
            "t_mom": TaskSpec(
                id="t_mom",
                type="transfer",
                stage=TaskStage.AWAITING_FUNDING_ADJUSTMENT,
                payload={
                    "amount": 40000.0,
                    "recipient_name": "mom",
                    "source_account_id": "acc-1",
                    "suggested_funding_plan": suggested_plan,
                    "funding_plan": None,
                },
            )
        },
        loaded_context={"language": "en", "accounts": []},
    )
    config: RunnableConfig = {"configurable": {"services": {}}, "recursion_limit": 50}
    runtime = build_interrupt_runtime(state=state, config=config)

    updates = await _input_shortcut_updates(state=state, runtime=runtime)

    assert updates is not None
    assert updates["pending_interrupt"] is None
    task = updates["tasks"]["t_mom"]
    assert task.stage == TaskStage.EXTRACTED
    assert task.payload["funding_plan"] is None
    assert task.payload["suggested_funding_plan"] is None


async def test_accepting_suggested_batch_funding_reaches_existing_confirmation_gate() -> None:
    access = _account("Access Bank", "acc_access", is_default=True)
    gtb = _account("GTBank", "acc_gtb")
    suggested_plan_mom = {
        "transfer_amount": "30000.00",
        "total_funded": "30000.00",
        "is_sufficient": True,
        "is_single_source": False,
        "planned_for_amount": "30000.00",
        "planned_for_source_account_id": access["id"],
        "planned_for_source_accounts": [],
        "planned_for_use_dual_accounts": False,
        "planned_for_explicit_split": {},
        "steps": [
            {
                "account_id": access["id"],
                "account_number": access["account_number"],
                "bank_name": "Access Bank",
                "amount": "30000.00",
            }
        ],
    }
    suggested_plan_ay = {
        **suggested_plan_mom,
        "steps": [
            {
                "account_id": gtb["id"],
                "account_number": gtb["account_number"],
                "bank_name": "GTBank",
                "amount": "30000.00",
            }
        ],
    }
    provider = _MockDirectDebitProvider({"acc_access": 30000.0, "acc_gtb": 30000.0})
    worker = _TransferNeedsConfirmationNoFundingPatch(provider)
    state = OrchestratorState(
        turn_directive=execution_test_directive(),
        user_id="u_batch_accept_to_confirmation",
        phone_number="2348000001010",
        channel="whatsapp",
        last_message_text="yes",
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t_mom", "t_ay"],
            fields_by_task={
                "t_mom": ["suggested_funding_plan", "amount", "source_accounts", "explicit_split"],
                "t_ay": ["suggested_funding_plan", "amount", "source_accounts", "explicit_split"],
            },
            prompt="Reply yes to confirm this funding.",
        ),
        waves=[["t_mom", "t_ay"]],
        current_wave_index=0,
        tasks={
            "t_mom": TaskSpec(
                id="t_mom",
                type="transfer",
                stage=TaskStage.AWAITING_FUNDING_ADJUSTMENT,
                payload={
                    "amount": 30000.0,
                    "recipient_name": "mom",
                    "recipient_account": "8067892221",
                    "recipient_bank_name": "Wema",
                    **_resolved_recipient("Pastor Bright"),
                    "source_account_id": access["id"],
                    "source_affinity_mode": "auto",
                    "suggested_funding_plan": suggested_plan_mom,
                    "funding_plan": None,
                },
            ),
            "t_ay": TaskSpec(
                id="t_ay",
                type="transfer",
                stage=TaskStage.AWAITING_FUNDING_ADJUSTMENT,
                payload={
                    "amount": 30000.0,
                    "recipient_name": "ay",
                    "recipient_account": "8080844362",
                    "recipient_bank_name": "Opay",
                    **_resolved_recipient("Yusuf Ibrahim"),
                    "source_account_id": access["id"],
                    "source_affinity_mode": "auto",
                    "suggested_funding_plan": suggested_plan_ay,
                    "funding_plan": None,
                },
            ),
        },
        loaded_context={"language": "en", "accounts": [access, gtb]},
    )
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}
    runtime = build_interrupt_runtime(state=state, config=config)

    acceptance_updates = await _input_shortcut_updates(state=state, runtime=runtime)
    assert acceptance_updates is not None
    state = state.model_copy(update=acceptance_updates)

    updates = await advance_wave(state, config)

    assert updates["pending_interrupt"].kind == "confirmation"
    assert updates["pending_interrupt"].task_ids == ["t_mom", "t_ay"]
    assert "Funding review" not in updates["outbox"][-1]["summary"]
    assert "Funding from:" in updates["outbox"][-1]["summary"]
    assert "????" not in updates["outbox"][-1]["summary"]
    assert "GTBank (···" in updates["outbox"][-1]["summary"]
    assert updates["tasks"]["t_mom"].payload["suggested_funding_plan"] is None
    assert updates["tasks"]["t_ay"].payload["suggested_funding_plan"] is None
    assert updates["tasks"]["t_mom"].payload["confirmation"].get("confirmed") is not True
    assert updates["tasks"]["t_ay"].payload["confirmation"].get("confirmed") is not True


async def test_batch_source_choice_defers_confirmation_until_recipient_review_is_accepted() -> None:
    access = _account("Access Bank", "acc_access", is_default=True)
    gtb = _account("GTBank", "acc_gtb")
    provider = _MockDirectDebitProvider({"acc_access": 30000.0, "acc_gtb": 30000.0})
    worker = _TransferResolvesRecipientThenNeedsConfirmation(provider)

    state = OrchestratorState(
        turn_directive=execution_test_directive(),
        user_id="u_batch_recipient_review_1",
        phone_number="2348000001006",
        channel="whatsapp",
        waves=[["t_mom", "t_ay"]],
        current_wave_index=0,
        tasks={
            "t_mom": TaskSpec(
                id="t_mom",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "amount": 30000.0,
                    "recipient_name": "mom",
                    "recipient_account": "8067892221",
                    "recipient_bank_name": "Wema",
                    "source_accounts": ["Access Bank", "GTBank"],
                    "use_dual_accounts": True,
                    "source_affinity_mode": "explicit",
                },
            ),
            "t_ay": TaskSpec(
                id="t_ay",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "amount": 30000.0,
                    "recipient_name": "ay",
                    "recipient_account": "8080844362",
                    "recipient_bank_name": "Opay",
                    "source_accounts": ["Access Bank", "GTBank"],
                    "use_dual_accounts": True,
                    "source_affinity_mode": "explicit",
                },
            ),
        },
        loaded_context={"language": "en", "accounts": [access, gtb]},
    )
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)

    assert updates["pending_interrupt"].kind == "input"
    assert updates["pending_interrupt"].fields_by_task == {
        "t_mom": ["recipient_review_confirmed"],
        "t_ay": ["recipient_review_confirmed"],
    }
    outbox_text = "\n".join(str(entry.get("text") or entry.get("summary") or "") for entry in updates["outbox"])
    assert "Recipient review" in outbox_text
    assert "Confirm Transactions" not in outbox_text
    assert "Funding from:" not in outbox_text
    assert "confirmation" not in updates["tasks"]["t_mom"].payload
    assert "confirmation" not in updates["tasks"]["t_ay"].payload


async def test_batch_funding_recoordinates_after_input_resume_before_confirmation() -> None:
    access = _account("Access Bank", "acc_access", is_default=True)
    gtb = _account("GTBank", "acc_gtb")
    first = _account("First Bank", "acc_first")
    provider = _MockDirectDebitProvider({"acc_access": 30000.0, "acc_gtb": 30000.0, "acc_first": 30000.0})
    worker = _TransferNeedsConfirmationWithDD(provider, access)

    state = OrchestratorState(
        turn_directive=execution_test_directive(),
        user_id="u_batch_resume_1",
        phone_number="2348000001004",
        channel="whatsapp",
        waves=[["t_mom", "t_ay"]],
        current_wave_index=0,
        tasks={
            "t_mom": TaskSpec(
                id="t_mom",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "amount": 40000.0,
                    "recipient_name": "mom",
                    "recipient_account": "8067892221",
                    "recipient_bank_name": "Wema",
                    **_resolved_recipient("Pastor Bright"),
                    "source_affinity_mode": "auto",
                },
            ),
            "t_ay": TaskSpec(
                id="t_ay",
                type="transfer",
                stage=TaskStage.AWAITING_CONFIRMATION,
                payload={
                    "amount": 30000.0,
                    "recipient_name": "ay",
                    "recipient_account": "2010000002",
                    "recipient_bank_name": "GTBank",
                    **_resolved_recipient("Yusuf Ibrahim"),
                    "source_account_id": access["id"],
                    "source_bank_name": "Access Bank",
                    "source_account_number": access["account_number"],
                    "source_affinity_mode": "auto",
                    "confirmation": {"summary": "Confirm ay", "snapshot": {"amount": 30000}},
                    "idempotency_key": "idem-ay",
                },
            ),
        },
        loaded_context={"language": "en", "accounts": [access, gtb, first]},
    )
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)

    assert worker.calls == []
    interrupt = updates["pending_interrupt"]
    assert interrupt.kind == "input"
    assert interrupt.task_ids == ["t_mom", "t_ay"]
    assert updates["tasks"]["t_mom"].stage == TaskStage.AWAITING_FUNDING_ADJUSTMENT
    assert updates["tasks"]["t_ay"].stage == TaskStage.AWAITING_FUNDING_ADJUSTMENT
    prompt = updates["outbox"][-1]["text"]
    assert "maximum of 2 pooled accounts" in prompt
    assert "₦60,000" in prompt
    assert "₦10,000" in prompt
    assert "????" not in prompt
    assert "Confirm Transfers" not in prompt


async def test_batch_funding_continues_same_batch_sibling_after_recipient_input_resume() -> None:
    access = _account("Access Bank", "acc_access", is_default=True)
    gtb = _account("GTBank", "acc_gtb")
    first = _account("First Bank", "acc_first")
    provider = _MockDirectDebitProvider({"acc_access": 30000.0, "acc_gtb": 30000.0, "acc_first": 30000.0})
    worker = _TransferNeedsConfirmationWithDD(provider, access)

    state = OrchestratorState(
        turn_directive=execution_test_directive(),
        user_id="u_batch_resume_2",
        phone_number="2348000001005",
        channel="whatsapp",
        last_message_text="8067892221, wema",
        last_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["t_mom"],
            fields_by_task={"t_mom": ["recipient_account", "recipient_bank_name"]},
            prompt="Please share the account number and bank for your mom.",
        ),
        waves=[["t_mom", "t_ay"]],
        current_wave_index=0,
        tasks={
            "t_mom": TaskSpec(
                id="t_mom",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "amount": 40000.0,
                    "recipient_name": "mom",
                    "recipient_account": "8067892221",
                    "recipient_bank_name": "Wema",
                    **_resolved_recipient("Pastor Bright"),
                    "source_affinity_mode": "auto",
                    "async_group_id": "batch-1",
                },
            ),
            "t_ay": TaskSpec(
                id="t_ay",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "amount": 30000.0,
                    "recipient_name": "ay",
                    "recipient_account": "2010000002",
                    "recipient_bank_name": "GTBank",
                    **_resolved_recipient("Yusuf Ibrahim"),
                    "source_affinity_mode": "auto",
                    "async_group_id": "batch-1",
                },
            ),
        },
        loaded_context={"language": "en", "accounts": [access, gtb, first]},
    )
    config: RunnableConfig = {"configurable": {"services": {"transfer": worker}}, "recursion_limit": 50}

    updates = await advance_wave(state, config)

    assert worker.calls == []
    interrupt = updates["pending_interrupt"]
    assert interrupt.kind == "input"
    assert interrupt.task_ids == ["t_mom", "t_ay"]
    assert updates["tasks"]["t_mom"].stage == TaskStage.AWAITING_FUNDING_ADJUSTMENT
    assert updates["tasks"]["t_ay"].stage == TaskStage.AWAITING_FUNDING_ADJUSTMENT
    prompt = updates["outbox"][-1]["text"]
    assert "maximum of 2 pooled accounts" in prompt
    assert "₦60,000" in prompt
    assert "????" not in prompt
    assert "Confirm Transfers" not in prompt
