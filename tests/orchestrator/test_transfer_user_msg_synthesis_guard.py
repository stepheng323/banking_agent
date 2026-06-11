from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.domain import PendingInterrupt, TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.accumulator import ExecutionAccumulator
from apps.chat.src.agent.orchestrator.workflows.execution.beneficiary_resolution import _normalize_beneficiary_rows
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext
from apps.chat.src.agent.orchestrator.workflows.execution.executors.transfer import TransferTaskExecutor
from apps.chat.src.agent.orchestrator.workflows.services import OrchestrationServices
from banking.runtime.results import TransactionOutcome, TransactionResult


class _CaptureTransferWorker:
    def __init__(self) -> None:
        self.last_payload: dict[str, Any] | None = None
        self.last_user_message: str | None = None
        self.last_context: dict[str, Any] | None = None

    async def run(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        del pin_verified
        self.last_payload = payload
        self.last_user_message = user_message
        self.last_context = context
        return TransactionResult(outcome=TransactionOutcome.OK, patch={})


class _BeneficiaryRepoStub:
    def __init__(
        self, *, search_rows: list[dict[str, Any]] | None = None, full_rows: list[dict[str, Any]] | None = None
    ) -> None:
        self.search_rows = search_rows or []
        self.full_rows = full_rows or []
        self.search_calls: list[tuple[str, str, str | None]] = []
        self.full_calls: list[tuple[str, str | None]] = []

    async def search_by_name(
        self, user_id: str, search_term: str, beneficiary_type: str | None = None
    ) -> list[dict[str, Any]]:
        self.search_calls.append((user_id, search_term, beneficiary_type))
        return self.search_rows

    async def get_by_user(self, user_id: str, beneficiary_type: str | None = None) -> list[dict[str, Any]]:
        self.full_calls.append((user_id, beneficiary_type))
        return self.full_rows


class _BeneficiaryLikeWithAccountProperty:
    def __init__(self) -> None:
        self.id = "bene-2"
        self.alias = "Tolu GTB"
        self.account_name = "Tolu Adeyemi"
        self.bank_name = "GTBank"
        self.bank_code = "058"
        self.beneficiary_type = "transfer"

    @property
    def account_number(self) -> str:
        return "2010000002"


def test_normalize_beneficiary_rows_preserves_decrypted_account_number_property() -> None:
    rows = _normalize_beneficiary_rows([_BeneficiaryLikeWithAccountProperty()])

    assert rows == [
        {
            "id": "bene-2",
            "alias": "Tolu GTB",
            "account_name": "Tolu Adeyemi",
            "bank_name": "GTBank",
            "bank_code": "058",
            "beneficiary_type": "transfer",
            "account_number": "2010000002",
        }
    ]


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
    ctx = ExecutionTurnContext(
        state=state,
        config=config,
        services=OrchestrationServices.from_mapping({"transfer": worker}),
        current_wave_len=1,
        accumulator=ExecutionAccumulator(state.tasks),
    )
    await TransferTaskExecutor().execute(task, "t1", ctx)
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
async def test_transfer_handler_drops_null_source_affinity_mode_before_worker() -> None:
    worker = _CaptureTransferWorker()
    task = TaskSpec(
        id="t1",
        type="transfer",
        stage=TaskStage.EXTRACTED,
        payload={
            "recipient_name": "Mum",
            "amount": 5000,
            "source_account_id": "acc_1",
            "source_affinity_mode": None,
        },
    )
    state = OrchestratorState(
        user_id="u_transfer_guard",
        phone_number="2348000000123",
        channel="whatsapp",
        last_message_text="Send again",
        loaded_context={"language": "en", "user_id": "u_transfer_guard", "accounts": [], "beneficiaries": []},
        tasks={"t1": task},
        waves=[["t1"]],
        current_wave_index=0,
    )
    config: RunnableConfig = {"configurable": {}, "recursion_limit": 50}
    ctx = ExecutionTurnContext(
        state=state,
        config=config,
        services=OrchestrationServices.from_mapping({"transfer": worker}),
        current_wave_len=1,
        accumulator=ExecutionAccumulator(state.tasks),
    )

    await TransferTaskExecutor().execute(task, "t1", ctx)

    assert worker.last_payload is not None
    assert "source_affinity_mode" not in worker.last_payload


@pytest.mark.asyncio
async def test_transfer_handler_prefers_scoped_confirmation_user_message_override() -> None:
    worker = _CaptureTransferWorker()
    task = TaskSpec(
        id="t1",
        type="transfer",
        stage=TaskStage.EXTRACTED,
        payload={
            "recipient_name": "Mum",
            "amount": 5000,
            "source_account_id": "acc_1",
            "pending_user_message": "for allowance",
            "confirmation_message_scoped": True,
        },
    )
    state = OrchestratorState(
        user_id="u_transfer_guard",
        phone_number="2348000000123",
        channel="whatsapp",
        last_message_text="The one for mum is allowance and tolu is transport",
        last_interrupt=PendingInterrupt(kind="confirmation", task_ids=["t1", "t2"]),
        loaded_context={"language": "en", "user_id": "u_transfer_guard", "accounts": [], "beneficiaries": []},
        tasks={"t1": task},
        waves=[["t1"]],
        current_wave_index=0,
    )
    config: RunnableConfig = {"configurable": {}, "recursion_limit": 50}
    ctx = ExecutionTurnContext(
        state=state,
        config=config,
        services=OrchestrationServices.from_mapping({"transfer": worker}),
        current_wave_len=1,
        accumulator=ExecutionAccumulator(state.tasks),
    )

    await TransferTaskExecutor().execute(task, "t1", ctx)

    assert worker.last_user_message == "for allowance"
    assert "pending_user_message" not in task.payload


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
    ctx = ExecutionTurnContext(
        state=state,
        config=config,
        services=OrchestrationServices.from_mapping({"transfer": worker}),
        current_wave_len=1,
        accumulator=ExecutionAccumulator(state.tasks),
    )

    await TransferTaskExecutor().execute(task, "t1", ctx)

    assert repo.search_calls == [("u_transfer_guard", "Mum", "transfer")]
    assert repo.full_calls == []
    assert worker.last_context is not None
    assert worker.last_context["beneficiaries"][0]["alias"] == "Mum"


@pytest.mark.asyncio
async def test_transfer_handler_reloads_beneficiaries_before_fresh_direct_extraction() -> None:
    worker = _CaptureTransferWorker()
    repo = _BeneficiaryRepoStub(
        full_rows=[
            {
                "id": "bene-1",
                "alias": "Tolu Access",
                "account_name": "Tolu Adebayo",
                "account_number": "2010000001",
                "bank_name": "Access Bank",
                "bank_code": "044",
                "beneficiary_type": "transfer",
            }
        ],
    )
    task = TaskSpec(
        id="t1",
        type="transfer",
        stage=TaskStage.EXTRACTED,
        payload={"amount": 10000},
    )
    state = OrchestratorState(
        user_id="u_transfer_guard",
        phone_number="2348000000123",
        channel="whatsapp",
        last_message_text="Send 10k to tolu adebayo",
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
    ctx = ExecutionTurnContext(
        state=state,
        config=config,
        services=OrchestrationServices.from_mapping({"transfer": worker}),
        current_wave_len=1,
        accumulator=ExecutionAccumulator(state.tasks),
    )

    await TransferTaskExecutor().execute(task, "t1", ctx)

    assert repo.search_calls == []
    assert repo.full_calls == [("u_transfer_guard", "transfer")]
    assert worker.last_context is not None
    assert worker.last_context["beneficiaries"][0]["account_name"] == "Tolu Adebayo"
    assert state.loaded_context["beneficiaries"][0]["account_number"] == "2010000001"


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
    ctx = ExecutionTurnContext(
        state=state,
        config=config,
        services=OrchestrationServices.from_mapping({"transfer": worker}),
        current_wave_len=1,
        accumulator=ExecutionAccumulator(state.tasks),
    )

    await TransferTaskExecutor().execute(task, "t1", ctx)

    assert repo.search_calls == [("u_transfer_guard", "Mum", "transfer")]
    assert repo.full_calls == [("u_transfer_guard", "transfer")]
    assert worker.last_context is not None
    assert worker.last_context["beneficiaries"][0]["alias"] == "Mum"


@pytest.mark.asyncio
async def test_transfer_handler_reloads_beneficiaries_for_skinny_candidate_selection_checkpoint() -> None:
    worker = _CaptureTransferWorker()
    repo = _BeneficiaryRepoStub(
        full_rows=[
            {
                "id": "bene-1",
                "alias": "Tolu Access",
                "account_name": "Tolu Adebayo",
                "account_number": "2010000001",
                "bank_name": "Access Bank",
                "bank_code": "044",
                "beneficiary_type": "transfer",
            },
            {
                "id": "bene-2",
                "alias": "Tolu GTB",
                "account_name": "Tolu Adeyemi",
                "account_number": "2010000002",
                "bank_name": "GTBank",
                "bank_code": "058",
                "beneficiary_type": "transfer",
            },
        ],
    )
    task = TaskSpec(
        id="t1",
        type="transfer",
        stage=TaskStage.EXTRACTED,
        payload={
            "recipient_name": "tolu",
            "amount": 2000,
            "beneficiary_candidates": [
                {
                    "index": 1,
                    "beneficiary_id": "bene-1",
                    "option_id": "bene:bene-1",
                    "label": "Tolu Adebayo • Access Bank • ****0001",
                },
                {
                    "index": 2,
                    "beneficiary_id": "bene-2",
                    "option_id": "bene:bene-2",
                    "label": "Tolu Adeyemi • GTBank • ****0002",
                },
            ],
        },
    )
    state = OrchestratorState(
        user_id="u_transfer_guard",
        phone_number="2348000000123",
        channel="whatsapp",
        last_message_text="Tolu Adeyemi • GTBan",
        loaded_context={
            "language": "en",
            "user_id": "u_transfer_guard",
            "accounts": [],
            "beneficiaries": [
                {
                    "id": "stale-bene",
                    "alias": "Mum",
                    "account_name": "Mercy Johnson",
                    "account_number": "8162511023",
                    "bank_name": "Opay",
                    "bank_code": "999991",
                    "beneficiary_type": "transfer",
                }
            ],
            "beneficiary_context_mode": "cache_only",
        },
        tasks={"t1": task},
        waves=[["t1"]],
        current_wave_index=0,
    )
    config: RunnableConfig = {"configurable": {"beneficiary_repo": repo}, "recursion_limit": 50}
    ctx = ExecutionTurnContext(
        state=state,
        config=config,
        services=OrchestrationServices.from_mapping({"transfer": worker}),
        current_wave_len=1,
        accumulator=ExecutionAccumulator(state.tasks),
    )

    await TransferTaskExecutor().execute(task, "t1", ctx)

    assert repo.search_calls == []
    assert repo.full_calls == [("u_transfer_guard", "transfer")]
    assert worker.last_context is not None
    assert [row["id"] for row in worker.last_context["beneficiaries"]] == ["bene-1", "bene-2"]


@pytest.mark.asyncio
async def test_transfer_handler_reloads_targeted_beneficiary_when_cache_preview_misses_recipient() -> None:
    worker = _CaptureTransferWorker()
    repo = _BeneficiaryRepoStub(
        search_rows=[
            {
                "id": "bene-2",
                "alias": "Tolu",
                "account_name": "Tolu Adedayo",
                "account_number": "0760505261",
                "bank_name": "First Bank",
                "bank_code": "999992",
                "beneficiary_type": "transfer",
            }
        ]
    )
    task = TaskSpec(
        id="t2",
        type="transfer",
        stage=TaskStage.EXTRACTED,
        payload={"recipient_name": "Tolu", "amount": 10000, "source_account_id": "acc_1"},
    )
    state = OrchestratorState(
        user_id="u_transfer_guard",
        phone_number="2348000000124",
        channel="whatsapp",
        last_message_text="send 10k to tolu",
        loaded_context={
            "language": "en",
            "user_id": "u_transfer_guard",
            "accounts": [],
            "beneficiaries": [
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
            "beneficiary_context_mode": "cache_only",
        },
        tasks={"t2": task},
        waves=[["t2"]],
        current_wave_index=0,
    )
    config: RunnableConfig = {"configurable": {"beneficiary_repo": repo}, "recursion_limit": 50}
    ctx = ExecutionTurnContext(
        state=state,
        config=config,
        services=OrchestrationServices.from_mapping({"transfer": worker}),
        current_wave_len=1,
        accumulator=ExecutionAccumulator(state.tasks),
    )

    await TransferTaskExecutor().execute(task, "t2", ctx)

    assert repo.search_calls == [("u_transfer_guard", "Tolu", "transfer")]
    assert repo.full_calls == []
    assert worker.last_context is not None
    assert worker.last_context["beneficiaries"][0]["alias"] == "Tolu"
