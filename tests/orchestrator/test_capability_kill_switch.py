import json
from pathlib import Path

import pytest

from apps.chat.src.agent.graphs.data.worker import DataWorker
from apps.chat.src.agent.graphs.faq.worker import FAQWorker
from apps.chat.src.agent.graphs.support.worker import SupportWorker
from apps.chat.src.agent.graphs.transfer.worker import TransferWorker
from apps.chat.src.agent.orchestrator.models.domain import FAQOutcome, SupportOutcome, TransactionOutcome
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.nodes.gate.pipeline.context import GateContext
from apps.chat.src.agent.orchestrator.nodes.gate.pipeline.domain_stages import _stage_data_domain
from apps.chat.src.agent.orchestrator.nodes.planner.task_flow import _build_planner_task_updates
from shared.policy.adapters import is_capability_supported
from shared.policy.loader import get_cached_policy, load_policy
from shared.policy.models import CapabilityPolicy
from shared.policy.service import capability_block_message
from shared.types.planner import PlannedTask, PlannerOutput, TaskParameters

CAPABILITY_POLICY_PATH = "config/capability_policy.json"
DATA_DISABLED_MESSAGE = (
    "Data purchase is temporarily unavailable. I can still help with transfers, airtime, balances, and transaction "
    "queries."
)
SUPPORT_DISABLED_MESSAGE = (
    "Support help is temporarily unavailable. I can still help with transfers, airtime/data purchase, balances, and "
    "transaction queries."
)
FAQ_DISABLED_MESSAGE = (
    "Banking help is temporarily unavailable. I can still help with transfers, airtime/data purchase, balances, and "
    "transaction queries."
)
SCHEDULE_DISABLED_MESSAGE = (
    "Scheduled payments are temporarily unavailable. I can still help with immediate transfers, airtime/data purchase, "
    "balances, and transaction queries."
)


def _policy_with_disabled_data() -> CapabilityPolicy:
    raw = load_policy(CAPABILITY_POLICY_PATH).model_dump()
    raw["capability_matrix"]["data"]["enabled"] = False
    raw["capability_matrix"]["data"]["limitation_message"] = DATA_DISABLED_MESSAGE
    return load_policy(CAPABILITY_POLICY_PATH).__class__.model_validate(raw)


def _install_disabled_domain_policy(tmp_path: Path, *, domain: str, message: str) -> None:
    raw = load_policy(CAPABILITY_POLICY_PATH).model_dump()
    raw["capability_matrix"][domain]["enabled"] = False
    raw["capability_matrix"][domain]["limitation_message"] = message
    policy_path = tmp_path / f"capability_policy_{domain}_disabled.json"
    policy_path.write_text(json.dumps(raw, ensure_ascii=True), encoding="utf-8")
    get_cached_policy(path=str(policy_path), force_reload=True)


def _install_disabled_data_policy(tmp_path: Path) -> None:
    _install_disabled_domain_policy(tmp_path, domain="data", message=DATA_DISABLED_MESSAGE)


def _reset_policy_cache() -> None:
    get_cached_policy(path=CAPABILITY_POLICY_PATH, force_reload=True)


def test_policy_helpers_treat_disabled_domain_as_unsupported() -> None:
    policy = _policy_with_disabled_data()

    assert not is_capability_supported("data", "buy_data", policy=policy)
    assert capability_block_message("data", "buy_data", policy=policy) == DATA_DISABLED_MESSAGE


def test_policy_helpers_treat_missing_action_as_unsupported() -> None:
    policy = load_policy(CAPABILITY_POLICY_PATH)

    assert not is_capability_supported("data", "refund_data", policy=policy)
    assert "refund data isn't available yet" in capability_block_message("data", "refund_data", policy=policy).lower()


def test_policy_helpers_treat_disabled_schedule_domain_as_unsupported() -> None:
    raw = load_policy(CAPABILITY_POLICY_PATH).model_dump()
    raw["capability_matrix"]["schedule"]["enabled"] = False
    raw["capability_matrix"]["schedule"]["limitation_message"] = SCHEDULE_DISABLED_MESSAGE
    policy = CapabilityPolicy.model_validate(raw)

    assert not is_capability_supported("schedule", "schedule_transfer", policy=policy)
    assert capability_block_message("schedule", "schedule_transfer", policy=policy) == SCHEDULE_DISABLED_MESSAGE


@pytest.mark.asyncio
async def test_planner_blocks_all_disabled_data_tasks(tmp_path: Path) -> None:
    _install_disabled_data_policy(tmp_path)
    try:
        planner_output = PlannerOutput(
            primary_intent="data",
            tasks=[
                PlannedTask(
                    task_id="data_1",
                    action="buy_data",
                    executor="data",
                    instruction="Buy 1GB data",
                    parameters=TaskParameters(plan="1GB", phone="08031234567", network="MTN"),
                    risk="MONEY_MOVE",
                )
            ],
        )

        updates = await _build_planner_task_updates(
            planner_output=planner_output,
            text="Buy 1GB data",
            locale="en",
            query_session_source=None,
            query_session_snapshot=None,
        )

        assert updates["new_tasks"] == {}
        assert updates["waves"] == []
        assert updates["capability_block_response"] == DATA_DISABLED_MESSAGE
    finally:
        _reset_policy_cache()


@pytest.mark.asyncio
async def test_planner_continues_supported_tasks_when_data_is_disabled(tmp_path: Path) -> None:
    _install_disabled_data_policy(tmp_path)
    try:
        planner_output = PlannerOutput(
            primary_intent="mixed",
            tasks=[
                PlannedTask(
                    task_id="transfer_1",
                    action="send_money",
                    executor="transfer",
                    instruction="Send 5k to Tolu",
                    parameters=TaskParameters(amount=5000, recipient="Tolu", recipient_name="Tolu"),
                    risk="MONEY_MOVE",
                ),
                PlannedTask(
                    task_id="data_1",
                    action="buy_data",
                    executor="data",
                    instruction="Buy 1GB data",
                    parameters=TaskParameters(plan="1GB", phone="08031234567", network="MTN"),
                    risk="MONEY_MOVE",
                ),
            ],
            is_complex=True,
        )

        updates = await _build_planner_task_updates(
            planner_output=planner_output,
            text="Please handle these tasks",
            locale="en",
            query_session_source=None,
            query_session_snapshot=None,
        )

        assert list(updates["new_tasks"]) == ["transfer_1"]
        assert updates["waves"] == [["transfer_1"]]
        assert updates["capability_policy_notice"] == DATA_DISABLED_MESSAGE
    finally:
        _reset_policy_cache()


@pytest.mark.asyncio
async def test_direct_data_gate_blocks_before_task_creation(tmp_path: Path) -> None:
    _install_disabled_data_policy(tmp_path)
    try:
        state = OrchestratorState(
            user_id="u_1",
            phone_number="2348000000000",
            last_message_text="Buy 1GB data",
            loaded_context={"language": "en"},
        )
        ctx = GateContext(
            state=state,
            config={"configurable": {}},
            redis_client=None,
            task_planner=None,
            conversation_responder=None,
            message_text="Buy 1GB data",
            current_locale="en",
            gate_updates={},
            live_pending_interrupt=False,
            phrase_heavy_fastpath_allowed=True,
        )

        result = await _stage_data_domain(ctx)

        assert result is not None
        assert result["final_response"] == DATA_DISABLED_MESSAGE
        assert "tasks" not in result
    finally:
        _reset_policy_cache()


@pytest.mark.asyncio
async def test_data_worker_blocks_buy_data_when_domain_disabled(tmp_path: Path) -> None:
    _install_disabled_data_policy(tmp_path)
    try:
        worker = DataWorker(
            extractor=None,
            bill_provider=None,
            transaction_repo=None,
            publisher=None,
        )

        result = await worker.run(
            payload={"action": "buy_data", "amount": 1000},
            context={"phone_number": "2348000000000", "language": "en"},
        )

        assert result.outcome == TransactionOutcome.FAILED
        assert result.error == DATA_DISABLED_MESSAGE
        assert result.patch == {"capability_blocked": True}
    finally:
        _reset_policy_cache()


@pytest.mark.asyncio
async def test_planner_blocks_disabled_schedule_transfer_task(tmp_path: Path) -> None:
    _install_disabled_domain_policy(tmp_path, domain="schedule", message=SCHEDULE_DISABLED_MESSAGE)
    try:
        planner_output = PlannerOutput(
            primary_intent="transfer",
            tasks=[
                PlannedTask(
                    task_id="transfer_1",
                    action="schedule_transfer",
                    executor="transfer",
                    instruction="Send 5k to Tolu tomorrow",
                    parameters=TaskParameters(amount=5000, recipient="Tolu", scheduled="tomorrow"),
                    risk="MONEY_MOVE",
                )
            ],
        )

        updates = await _build_planner_task_updates(
            planner_output=planner_output,
            text="Send 5k to Tolu tomorrow",
            locale="en",
            query_session_source=None,
            query_session_snapshot=None,
        )

        assert updates["new_tasks"] == {}
        assert updates["capability_block_response"] == SCHEDULE_DISABLED_MESSAGE
    finally:
        _reset_policy_cache()


@pytest.mark.asyncio
async def test_planner_blocks_disabled_schedule_inferred_from_send_money_task(tmp_path: Path) -> None:
    _install_disabled_domain_policy(tmp_path, domain="schedule", message=SCHEDULE_DISABLED_MESSAGE)
    try:
        planner_output = PlannerOutput(
            primary_intent="transfer",
            tasks=[
                PlannedTask(
                    task_id="transfer_1",
                    action="send_money",
                    executor="transfer",
                    instruction="Send 5k to Tolu tomorrow",
                    parameters=TaskParameters(amount=5000, recipient="Tolu", scheduled="tomorrow"),
                    risk="MONEY_MOVE",
                )
            ],
        )

        updates = await _build_planner_task_updates(
            planner_output=planner_output,
            text="Send 5k to Tolu tomorrow",
            locale="en",
            query_session_source=None,
            query_session_snapshot=None,
        )

        assert updates["new_tasks"] == {}
        assert updates["capability_block_response"] == SCHEDULE_DISABLED_MESSAGE
    finally:
        _reset_policy_cache()


@pytest.mark.asyncio
async def test_transfer_worker_blocks_scheduling_when_schedule_domain_disabled(tmp_path: Path) -> None:
    _install_disabled_domain_policy(tmp_path, domain="schedule", message=SCHEDULE_DISABLED_MESSAGE)
    try:
        worker = TransferWorker(
            validation_service=None,
            publisher=None,
            extractor=None,
            resolver_provider=None,
            bank_cache=None,
            transaction_repo=None,
            dd_provider=None,
            redis_client=None,
        )

        result = await worker.run(
            payload={"action": "schedule_transfer", "amount": 5000},
            context={"phone_number": "2348000000000", "language": "en"},
            user_message="Send 5k to Tolu tomorrow",
        )

        assert result.outcome == TransactionOutcome.FAILED
        assert result.error == SCHEDULE_DISABLED_MESSAGE
        assert result.patch == {"capability_blocked": True}
    finally:
        _reset_policy_cache()


@pytest.mark.asyncio
async def test_planner_blocks_disabled_support_task_alias(tmp_path: Path) -> None:
    _install_disabled_domain_policy(tmp_path, domain="support", message=SUPPORT_DISABLED_MESSAGE)
    try:
        planner_output = PlannerOutput(
            primary_intent="support",
            tasks=[
                PlannedTask(
                    task_id="support_1",
                    action="report_issue",
                    executor="support",
                    instruction="My transfer failed",
                    parameters=TaskParameters(),
                    risk="READ_ONLY",
                )
            ],
        )

        updates = await _build_planner_task_updates(
            planner_output=planner_output,
            text="My transfer failed",
            locale="en",
            query_session_source=None,
            query_session_snapshot=None,
        )

        assert updates["new_tasks"] == {}
        assert updates["capability_block_response"] == SUPPORT_DISABLED_MESSAGE
    finally:
        _reset_policy_cache()


@pytest.mark.asyncio
async def test_planner_blocks_disabled_faq_task(tmp_path: Path) -> None:
    _install_disabled_domain_policy(tmp_path, domain="faq", message=FAQ_DISABLED_MESSAGE)
    try:
        planner_output = PlannerOutput(
            primary_intent="faq",
            tasks=[
                PlannedTask(
                    task_id="faq_1",
                    action="answer_question",
                    executor="faq",
                    instruction="What are the transfer fees?",
                    parameters=TaskParameters(),
                    risk="READ_ONLY",
                )
            ],
        )

        updates = await _build_planner_task_updates(
            planner_output=planner_output,
            text="What are the transfer fees?",
            locale="en",
            query_session_source=None,
            query_session_snapshot=None,
        )

        assert updates["new_tasks"] == {}
        assert updates["capability_block_response"] == FAQ_DISABLED_MESSAGE
    finally:
        _reset_policy_cache()


@pytest.mark.asyncio
async def test_support_worker_blocks_when_domain_disabled(tmp_path: Path) -> None:
    _install_disabled_domain_policy(tmp_path, domain="support", message=SUPPORT_DISABLED_MESSAGE)
    try:
        worker = SupportWorker(
            llm=None,
            transaction_repo=None,
            actionable_message_repo=None,
            redis_client=None,
        )

        result = await worker.run(
            payload={},
            context={"phone_number": "2348000000000", "language": "en"},
            user_message="My transfer failed",
        )

        assert result.outcome == SupportOutcome.OK
        assert result.response == SUPPORT_DISABLED_MESSAGE
        assert result.final_message == SUPPORT_DISABLED_MESSAGE
    finally:
        _reset_policy_cache()


@pytest.mark.asyncio
async def test_faq_worker_blocks_when_domain_disabled(tmp_path: Path) -> None:
    _install_disabled_domain_policy(tmp_path, domain="faq", message=FAQ_DISABLED_MESSAGE)
    try:
        worker = FAQWorker(llm=None, get_db=lambda: None)

        result = await worker.run(
            payload={},
            context={"phone_number": "2348000000000", "language": "en"},
            user_message="What are the transfer fees?",
        )

        assert result.outcome == FAQOutcome.OK
        assert result.response == FAQ_DISABLED_MESSAGE
    finally:
        _reset_policy_cache()
