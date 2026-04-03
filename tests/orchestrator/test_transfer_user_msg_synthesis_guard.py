from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig

from apps.core.src.agent.orchestrator.execution.handlers import (
    ExecutionAggregation,
    ExecutionContext,
    handle_transfer_task,
)
from apps.core.src.agent.orchestrator.models.domain import TaskSpec, TaskStage, TransactionOutcome, TransactionResult
from apps.core.src.agent.orchestrator.models.state import OrchestratorState


class _CaptureTransferWorker:
    def __init__(self) -> None:
        self.last_user_message: str | None = None
        self.last_context: dict[str, Any] | None = None

    async def run(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del payload, pin_verified
        self.last_user_message = user_message
        self.last_context = context
        return TransactionResult(outcome=TransactionOutcome.OK, patch={})


class _BeneficiaryRepoStub:
    def __init__(self, *, search_rows: list[dict[str, Any]] | None = None, full_rows: list[dict[str, Any]] | None = None) -> None:
        self.search_rows = search_rows or []
        self.full_rows = full_rows or []
        self.search_calls: list[tuple[str, str, str | None]] = []
        self.full_calls: list[tuple[str, str | None]] = []

    async def search_by_name(self, user_id: str, search_term: str, beneficiary_type: str | None = None) -> list[dict[str, Any]]:
        self.search_calls.append((user_id, search_term, beneficiary_type))
        return self.search_rows

    async def get_by_user(self, user_id: str, beneficiary_type: str | None = None) -> list[dict[str, Any]]:
        self.full_calls.append((user_id, beneficiary_type))
        return self.full_rows


async def _run_transfer_with_message(last_message_text: str | None) -> str | None:
    worker = _CaptureTransferWorker()
    task = TaskSpec(
        id="t1",
        type="transfer",
        stage=TaskStage.EXTRACTED,
        payload={
            "recipient_name": "Mum",
            "amount": 5000,
            "source_account_id": "acc_1",
        },
    )
    state = OrchestratorState(
        user_id="u_transfer_guard",
        phone_number="2348000000123",
        channel="whatsapp",
        last_message_text=last_message_text,
        loaded_context={"language": "en", "user_id": "u_transfer_guard", "accounts": [], "beneficiaries": []},
        tasks={"t1": task},
        waves=[["t1"]],
        current_wave_index=0,
    )
    config: RunnableConfig = {"configurable": {}, "recursion_limit": 50}
    ctx = ExecutionContext(
        state=state,
        config=config,
        services={"transfer": worker},
        current_wave_len=1,
        agg=ExecutionAggregation(state.tasks),
    )
    await handle_transfer_task(task, "t1", ctx)
    return worker.last_user_message


@pytest.mark.asyncio
async def test_transfer_handler_preserves_explicit_update_message() -> None:
    user_message = await _run_transfer_with_message("Change to 25k")
    assert user_message == "Change to 25k"


@pytest.mark.asyncio
async def test_transfer_handler_synthesizes_when_message_missing() -> None:
    user_message = await _run_transfer_with_message(None)
    assert user_message == "Send 5000 to Mum"


@pytest.mark.asyncio
async def test_transfer_handler_synthesizes_when_message_is_whitespace_only() -> None:
    user_message = await _run_transfer_with_message("   ")
    assert user_message == "Send 5000 to Mum"


@pytest.mark.asyncio
async def test_transfer_handler_uses_targeted_beneficiary_reload_for_cache_only_mode() -> None:
    worker = _CaptureTransferWorker()
    repo = _BeneficiaryRepoStub(
        search_rows=[
            {
                "id": "bene-1",
                "alias": "Mum",
                "account_name": "Mercy Johnson",
                "account_number": "8162511023",
                "bank_name": "Opay",
                "bank_code": "999991",
                "beneficiary_type": "transfer",
            }
        ]
    )
    task = TaskSpec(
        id="t1",
        type="transfer",
        stage=TaskStage.EXTRACTED,
        payload={"recipient_name": "Mum", "amount": 5000, "source_account_id": "acc_1"},
    )
    state = OrchestratorState(
        user_id="u_transfer_guard",
        phone_number="2348000000123",
        channel="whatsapp",
        last_message_text="send 5k to mum",
        loaded_context={
            "language": "en",
            "user_id": "u_transfer_guard",
            "accounts": [],
            "beneficiaries": [],
            "beneficiary_context_mode": "cache_only",
        },
        tasks={"t1": task},
        waves=[["t1"]],
        current_wave_index=0,
    )
    config: RunnableConfig = {"configurable": {"beneficiary_repo": repo}, "recursion_limit": 50}
    ctx = ExecutionContext(
        state=state,
        config=config,
        services={"transfer": worker},
        current_wave_len=1,
        agg=ExecutionAggregation(state.tasks),
    )

    await handle_transfer_task(task, "t1", ctx)

    assert repo.search_calls == [("u_transfer_guard", "Mum", "transfer")]
    assert repo.full_calls == []
    assert worker.last_context is not None
    assert worker.last_context["beneficiaries"][0]["alias"] == "Mum"


@pytest.mark.asyncio
async def test_transfer_handler_falls_back_to_full_beneficiary_reload_after_targeted_miss() -> None:
    worker = _CaptureTransferWorker()
    repo = _BeneficiaryRepoStub(
        search_rows=[],
        full_rows=[
            {
                "id": "bene-1",
                "alias": "Mum",
                "account_name": "Mercy Johnson",
                "account_number": "8162511023",
                "bank_name": "Opay",
                "bank_code": "999991",
                "beneficiary_type": "transfer",
            }
        ],
    )
    task = TaskSpec(
        id="t1",
        type="transfer",
        stage=TaskStage.EXTRACTED,
        payload={"recipient_name": "Mum", "amount": 5000, "source_account_id": "acc_1"},
    )
    state = OrchestratorState(
        user_id="u_transfer_guard",
        phone_number="2348000000123",
        channel="whatsapp",
        last_message_text="send 5k to mum",
        loaded_context={
            "language": "en",
            "user_id": "u_transfer_guard",
            "accounts": [],
            "beneficiaries": [],
            "beneficiary_context_mode": "cache_only",
        },
        tasks={"t1": task},
        waves=[["t1"]],
        current_wave_index=0,
    )
    config: RunnableConfig = {"configurable": {"beneficiary_repo": repo}, "recursion_limit": 50}
    ctx = ExecutionContext(
        state=state,
        config=config,
        services={"transfer": worker},
        current_wave_len=1,
        agg=ExecutionAggregation(state.tasks),
    )

    await handle_transfer_task(task, "t1", ctx)

    assert repo.search_calls == [("u_transfer_guard", "Mum", "transfer")]
    assert repo.full_calls == [("u_transfer_guard", "transfer")]
    assert worker.last_context is not None
    assert worker.last_context["beneficiaries"][0]["alias"] == "Mum"
