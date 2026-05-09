"""Dependency-wave planning and execution tests."""

import pytest
from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.models.domain import AccountOutcome, AccountResult, TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.nodes.execution import advance_wave
from apps.chat.src.agent.orchestrator.nodes.planner import plan_tasks
from shared.types.planner import PlannedTask, PlannerClause, PlannerOutput, RecipientAllocation, TaskParameters


class _MockPlanner:
    def __init__(self, output: PlannerOutput) -> None:
        self._output = output

    async def plan_tasks(self, phone_number: str, text: str, *, context: str = "None", prompt_signals: object | None = None) -> PlannerOutput:
        del phone_number, text, context
        return self._output


class _AccountWorker:
    async def run(
        self,
        payload: dict,
        context: dict,
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> AccountResult:
        del payload, context, user_message, pin_verified
        return AccountResult(outcome=AccountOutcome.OK, response="balance")


class _PlannerWithLegacyRepairMethods:
    def __init__(self, output: PlannerOutput) -> None:
        self._output = output
        self.plan_calls = 0
        self.review_calls = 0
        self.repair_calls = 0

    async def plan_tasks(self, phone_number: str, text: str, *, context: str = "None", prompt_signals: object | None = None) -> PlannerOutput:
        del phone_number, text, context
        self.plan_calls += 1
        return self._output

    async def review_task_completeness(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        self.review_calls += 1
        raise AssertionError("planner review path should not be invoked")

    async def repair_underproduced_plan(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        self.repair_calls += 1
        raise AssertionError("planner repair path should not be invoked")


class _SignalAwarePlanner:
    def __init__(self, output: PlannerOutput) -> None:
        self._output = output
        self.plan_calls = 0
        self.last_prompt_signals: object | None = None

    async def plan_tasks(self, phone_number: str, text: str, *, context: str = "None", prompt_signals: object | None = None) -> PlannerOutput:
        del phone_number, text, context
        self.plan_calls += 1
        self.last_prompt_signals = prompt_signals
        return self._output


@pytest.mark.asyncio
async def test_planner_builds_dependency_aware_waves_for_mixed_request() -> None:
    planner_output = PlannerOutput(
        primary_intent="mixed",
        is_complex=True,
        tasks=[
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 10000 to Mum",
                parameters=TaskParameters(amount=10000, recipient="Mum"),
                risk="MONEY_MOVE",
            ),
            PlannedTask(
                task_id="t2",
                action="send_money",
                executor="transfer",
                instruction="Send 10000 to Dad",
                parameters=TaskParameters(amount=10000, recipient="Dad"),
                risk="MONEY_MOVE",
            ),
            PlannedTask(
                task_id="t3",
                action="check_balance",
                executor="account",
                instruction="Show my balance",
                parameters=TaskParameters(),
                depends_on=["t1", "t2"],
                risk="READ_ONLY",
            ),
        ],
    )
    state = OrchestratorState(
        user_id="u_dep_1",
        phone_number="2348111111101",
        channel="whatsapp",
        last_message_text="send 10k to mum and dad then show my balance",
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _MockPlanner(planner_output), "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert updates["waves"] == [["t1", "t2"], ["t3"]]


@pytest.mark.asyncio
async def test_planner_does_not_call_legacy_review_or_repair_paths() -> None:
    planner_output = PlannerOutput(
        primary_intent="transfer",
        is_complex=False,
        tasks=[
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to Mum",
                parameters=TaskParameters(amount=10000, recipient="Mum"),
                risk="MONEY_MOVE",
            ),
        ],
    )
    planner = _PlannerWithLegacyRepairMethods(planner_output)
    state = OrchestratorState(
        user_id="u_dep_lowcall_1",
        phone_number="2348111111110",
        channel="whatsapp",
        last_message_text="send 10k to mum",
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": planner, "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert planner.plan_calls == 1
    assert planner.review_calls == 0
    assert planner.repair_calls == 0
    assert list(updates["tasks"].keys()) == ["t1"]


@pytest.mark.asyncio
async def test_planner_uses_expected_executor_signals_in_single_call() -> None:
    planner_output = PlannerOutput(
        primary_intent="mixed",
        is_complex=True,
        tasks=[
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to Mum",
                parameters=TaskParameters(amount=10000, recipient="Mum"),
                risk="MONEY_MOVE",
            ),
            PlannedTask(
                task_id="t2",
                action="buy_airtime",
                executor="airtime",
                instruction="Buy 5k airtime",
                parameters=TaskParameters(amount=5000, is_self=True),
                risk="MONEY_MOVE",
            ),
        ],
    )
    planner = _SignalAwarePlanner(planner_output)
    state = OrchestratorState(
        user_id="u_dep_retry_1",
        phone_number="2348111111111",
        channel="whatsapp",
        last_message_text="send 10k to mum and buy me 5k airtime",
        preplanner_expected_transaction_executors=["transfer", "airtime"],
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": planner, "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert planner.plan_calls == 1
    assert set(updates["tasks"].keys()) == {"t1", "t2"}
    assert updates["waves"] == [["t1", "t2"]]
    assert planner.last_prompt_signals is not None
    assert getattr(planner.last_prompt_signals, "expected_transaction_executors", ()) == ("transfer", "airtime")


@pytest.mark.asyncio
async def test_planner_strips_transaction_depends_on_for_batch_auth_collection() -> None:
    planner_output = PlannerOutput(
        primary_intent="mixed",
        is_complex=True,
        tasks=[
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to Mum",
                parameters=TaskParameters(amount=10000, recipient="Mum"),
                risk="MONEY_MOVE",
            ),
            PlannedTask(
                task_id="t2",
                action="buy_airtime",
                executor="airtime",
                instruction="Buy me 5k airtime",
                parameters=TaskParameters(amount=5000, is_self=True),
                depends_on=["t1"],
                risk="MONEY_MOVE",
            ),
        ],
    )
    state = OrchestratorState(
        user_id="u_dep_strip_1",
        phone_number="2348111111112",
        channel="whatsapp",
        last_message_text="send 10k to mum then buy me 5k airtime",
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _MockPlanner(planner_output), "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert updates["waves"] == [["t1", "t2"]]
    assert updates["tasks"]["t2"].depends_on == []


@pytest.mark.asyncio
async def test_planner_keeps_non_transaction_dependencies_when_stripping_transaction_edges() -> None:
    planner_output = PlannerOutput(
        primary_intent="mixed",
        is_complex=True,
        tasks=[
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to Mum",
                parameters=TaskParameters(amount=10000, recipient="Mum"),
                risk="MONEY_MOVE",
            ),
            PlannedTask(
                task_id="t2",
                action="buy_airtime",
                executor="airtime",
                instruction="Buy me 5k airtime",
                parameters=TaskParameters(amount=5000, is_self=True),
                depends_on=["t1"],
                risk="MONEY_MOVE",
            ),
            PlannedTask(
                task_id="t3",
                action="check_balance",
                executor="account",
                instruction="Show my balance",
                parameters=TaskParameters(),
                depends_on=["t1", "t2"],
                risk="READ_ONLY",
            ),
        ],
    )
    state = OrchestratorState(
        user_id="u_dep_strip_2",
        phone_number="2348111111113",
        channel="whatsapp",
        last_message_text="send 10k to mum then buy me 5k airtime then show my balance",
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _MockPlanner(planner_output), "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert updates["tasks"]["t2"].depends_on == []
    assert updates["tasks"]["t3"].depends_on == ["t1", "t2"]
    assert updates["waves"] == [["t1", "t2"], ["t3"]]


@pytest.mark.asyncio
async def test_planner_fans_out_single_transfer_when_text_has_multiple_recipients() -> None:
    planner_output = PlannerOutput(
        primary_intent="transfer",
        is_complex=False,
        tasks=[
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to Mum",
                parameters=TaskParameters(amount=10000, recipient="Mum"),
                risk="MONEY_MOVE",
            ),
        ],
    )
    state = OrchestratorState(
        user_id="u_dep_fanout_1",
        phone_number="2348111111191",
        channel="whatsapp",
        last_message_text="send 10k to mum and tolu",
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _MockPlanner(planner_output), "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert set(updates["tasks"].keys()) == {"t1", "t1_r2"}
    assert updates["tasks"]["t1"].payload.get("recipient_name") == "mum"
    assert updates["tasks"]["t1_r2"].payload.get("recipient_name") == "tolu"
    assert updates["tasks"]["t1"].payload.get("recipient_binding_source") == "fanout"
    assert updates["tasks"]["t1_r2"].payload.get("recipient_binding_source") == "fanout"
    assert updates["tasks"]["t1"].payload.get("amount") == 10000
    assert updates["tasks"]["t1_r2"].payload.get("amount") == 10000


@pytest.mark.asyncio
async def test_planner_repairs_missing_balance_task_from_clause_decomposition() -> None:
    planner_output = PlannerOutput(
        primary_intent="mixed",
        is_complex=True,
        clauses=[
            PlannerClause(
                clause_index=1,
                text="Send 4k to gaines",
                intent_family="transfer",
                extracted_fields={"recipient_name": "gaines", "amount": 4000},
            ),
            PlannerClause(
                clause_index=2,
                text="2k airtime for 0816 251 1024",
                intent_family="airtime",
                extracted_fields={"amount": 2000, "recipient_phone": "08162511024"},
            ),
            PlannerClause(
                clause_index=3,
                text="show my final balance",
                intent_family="account_query",
                extracted_fields={"account_action_hint": "check_balance"},
            ),
        ],
        tasks=[
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 4k to gaines",
                parameters=TaskParameters(amount=4000, recipient="gaines"),
                risk="MONEY_MOVE",
                source_clause_index=1,
            ),
            PlannedTask(
                task_id="t2",
                action="buy_airtime",
                executor="airtime",
                instruction="2k airtime for 0816 251 1024",
                parameters=TaskParameters(amount=2000, recipient_phone="08162511024"),
                risk="MONEY_MOVE",
                source_clause_index=2,
            ),
        ],
    )
    state = OrchestratorState(
        user_id="u_dep_clause_1",
        phone_number="2348111111193",
        channel="whatsapp",
        last_message_text="Send 4k to gaines, but 2k airtime for 0816 251 1024 and show my final balance",
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _MockPlanner(planner_output), "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert set(updates["tasks"].keys()) == {"t1", "t2", "account_clause_3"}
    assert updates["tasks"]["account_clause_3"].type == "account"
    assert updates["tasks"]["account_clause_3"].payload["action"] == "check_balance"
    assert updates["tasks"]["account_clause_3"].depends_on == ["t1", "t2"]
    assert updates["waves"] == [["t1", "t2"], ["account_clause_3"]]


@pytest.mark.asyncio
async def test_planner_repairs_transfer_recipient_from_transfer_clause_when_read_only_clause_leaks() -> None:
    planner_output = PlannerOutput(
        primary_intent="mixed",
        is_complex=True,
        clauses=[
            PlannerClause(
                clause_index=1,
                text="Send 4k to gaines",
                intent_family="transfer",
                extracted_fields={"recipient_name": "gaines", "amount": 4000},
            ),
            PlannerClause(
                clause_index=2,
                text="show my final balance",
                intent_family="account_query",
                extracted_fields={"account_action_hint": "check_balance"},
            ),
        ],
        tasks=[
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 4k to gaines, but show my final balance",
                parameters=TaskParameters(amount=4000, recipient_name="show my final balance"),
                risk="MONEY_MOVE",
            ),
        ],
    )
    state = OrchestratorState(
        user_id="u_dep_clause_2",
        phone_number="2348111111194",
        channel="whatsapp",
        last_message_text="Send 4k to gaines, but show my final balance",
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _MockPlanner(planner_output), "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert updates["tasks"]["t1"].payload["recipient_name"] == "gaines"
    assert updates["tasks"]["t1"].payload["instruction"] == "Send 4k to gaines"
    assert updates["tasks"]["t1"].payload["source_clause_index"] == 1
    assert updates["tasks"]["account_clause_2"].type == "account"
    assert updates["tasks"]["account_clause_2"].payload["action"] == "check_balance"
    assert updates["tasks"]["account_clause_2"].payload["instruction"] == "show my final balance"
    assert updates["waves"] == [["t1"], ["account_clause_2"]]


@pytest.mark.asyncio
async def test_planner_repairs_transfer_recipient_from_non_english_read_only_clause_leak() -> None:
    planner_output = PlannerOutput(
        primary_intent="mixed",
        is_complex=True,
        clauses=[
            PlannerClause(
                clause_index=1,
                text="Aika 4k zuwa gaines",
                intent_family="transfer",
                extracted_fields={"recipient_name": "gaines", "amount": 4000},
            ),
            PlannerClause(
                clause_index=2,
                text="nuna min final balance dina",
                intent_family="account_query",
                extracted_fields={"account_action_hint": "check_balance"},
            ),
        ],
        tasks=[
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Aika 4k zuwa gaines kuma nuna min final balance dina",
                parameters=TaskParameters(amount=4000, recipient_name="nuna min final balance dina"),
                risk="MONEY_MOVE",
            ),
        ],
    )
    state = OrchestratorState(
        user_id="u_dep_clause_2b",
        phone_number="2348111111195",
        channel="whatsapp",
        last_message_text="Aika 4k zuwa gaines kuma nuna min final balance dina",
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _MockPlanner(planner_output), "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert updates["tasks"]["t1"].payload["recipient_name"] == "gaines"
    assert updates["tasks"]["t1"].payload["instruction"] == "Aika 4k zuwa gaines"
    assert updates["tasks"]["t1"].payload["source_clause_index"] == 1
    assert updates["tasks"]["account_clause_2"].type == "account"
    assert updates["tasks"]["account_clause_2"].payload["action"] == "check_balance"
    assert updates["tasks"]["account_clause_2"].payload["instruction"] == "nuna min final balance dina"
    assert updates["waves"] == [["t1"], ["account_clause_2"]]


@pytest.mark.asyncio
async def test_planner_fans_out_recipient_split_between_recipients() -> None:
    planner_output = PlannerOutput(
        primary_intent="transfer",
        is_complex=False,
        tasks=[
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Split 20k 70/30 between Mum and Gaines",
                parameters=TaskParameters(
                    amount=20000,
                    recipient_allocations=[
                        RecipientAllocation(recipient_name="Mum", amount=14000.0),
                        RecipientAllocation(recipient_name="Gaines", amount=6000.0),
                    ],
                ),
                risk="MONEY_MOVE",
            ),
        ],
    )
    state = OrchestratorState(
        user_id="u_dep_fanout_split_1",
        phone_number="2348111111194",
        channel="whatsapp",
        last_message_text="split 20k 70/30 btw mum and gaines",
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _MockPlanner(planner_output), "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert set(updates["tasks"].keys()) == {"t1", "t1_r2"}
    assert updates["tasks"]["t1"].payload.get("recipient_name") == "Mum"
    assert updates["tasks"]["t1"].payload.get("amount") == 14000.0
    assert updates["tasks"]["t1"].payload.get("recipient_allocations") is None
    assert updates["tasks"]["t1"].payload.get("explicit_split") is None
    assert updates["tasks"]["t1_r2"].payload.get("recipient_name") == "Gaines"
    assert updates["tasks"]["t1_r2"].payload.get("amount") == 6000.0
    assert updates["tasks"]["t1_r2"].payload.get("recipient_allocations") is None
    assert updates["tasks"]["t1_r2"].payload.get("explicit_split") is None
    assert updates["waves"] == [["t1", "t1_r2"]]


@pytest.mark.asyncio
async def test_planner_fans_out_three_way_recipient_allocations_batch() -> None:
    planner_output = PlannerOutput(
        primary_intent="transfer",
        is_complex=False,
        tasks=[
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 10k each to Mum, Tolu and Doyin",
                parameters=TaskParameters(
                    amount=30000,
                    recipient_allocations=[
                        RecipientAllocation(recipient_name="Mum", amount=10000.0),
                        RecipientAllocation(recipient_name="Tolu", amount=10000.0),
                        RecipientAllocation(recipient_name="Doyin", amount=10000.0),
                    ],
                ),
                risk="MONEY_MOVE",
            ),
        ],
    )
    state = OrchestratorState(
        user_id="u_dep_fanout_split_2",
        phone_number="2348111111196",
        channel="whatsapp",
        last_message_text="send 10k each to mum, tolu and doyin",
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _MockPlanner(planner_output), "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert set(updates["tasks"].keys()) == {"t1", "t1_r2", "t1_r3"}
    assert updates["tasks"]["t1"].payload.get("recipient_name") == "Mum"
    assert updates["tasks"]["t1"].payload.get("amount") == 10000.0
    assert updates["tasks"]["t1_r2"].payload.get("recipient_name") == "Tolu"
    assert updates["tasks"]["t1_r2"].payload.get("amount") == 10000.0
    assert updates["tasks"]["t1_r3"].payload.get("recipient_name") == "Doyin"
    assert updates["tasks"]["t1_r3"].payload.get("amount") == 10000.0
    assert updates["waves"] == [["t1", "t1_r2", "t1_r3"]]


@pytest.mark.asyncio
async def test_planner_does_not_fanout_source_account_explicit_split_as_recipients() -> None:
    planner_output = PlannerOutput(
        primary_intent="transfer",
        is_complex=False,
        tasks=[
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Split 20k from Access and GTB to Mum",
                parameters=TaskParameters(
                    amount=20000,
                    recipient="Mum",
                    explicit_split={"Access": 10000.0, "GTB": 10000.0},
                ),
                risk="MONEY_MOVE",
            ),
        ],
    )
    state = OrchestratorState(
        user_id="u_dep_source_split_1",
        phone_number="2348111111195",
        channel="whatsapp",
        last_message_text="split 20k from access and gtb to mum",
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _MockPlanner(planner_output), "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert set(updates["tasks"].keys()) == {"t1"}
    assert updates["tasks"]["t1"].payload.get("recipient_name") == "Mum"
    assert updates["tasks"]["t1"].payload.get("explicit_split") == {"Access": 10000.0, "GTB": 10000.0}


@pytest.mark.asyncio
async def test_planner_fanout_rewrites_downstream_dependencies() -> None:
    planner_output = PlannerOutput(
        primary_intent="mixed",
        is_complex=True,
        tasks=[
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to Mum",
                parameters=TaskParameters(amount=10000, recipient="Mum"),
                risk="MONEY_MOVE",
            ),
            PlannedTask(
                task_id="t2",
                action="check_balance",
                executor="account",
                instruction="Show my balance",
                parameters=TaskParameters(),
                depends_on=["t1"],
                risk="READ_ONLY",
            ),
        ],
    )
    state = OrchestratorState(
        user_id="u_dep_fanout_2",
        phone_number="2348111111192",
        channel="whatsapp",
        last_message_text="send 10k to mum and tolu then show my balance",
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _MockPlanner(planner_output), "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert set(updates["tasks"].keys()) == {"t1", "t1_r2", "t2"}
    assert updates["tasks"]["t2"].depends_on == ["t1", "t1_r2"]
    assert updates["waves"] == [["t1", "t1_r2"], ["t2"]]


@pytest.mark.asyncio
async def test_planner_does_not_fanout_when_planner_already_emits_multiple_transfer_tasks() -> None:
    planner_output = PlannerOutput(
        primary_intent="transfer",
        is_complex=True,
        tasks=[
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to Mum",
                parameters=TaskParameters(amount=10000, recipient="Mum"),
                risk="MONEY_MOVE",
            ),
            PlannedTask(
                task_id="t2",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to Tolu",
                parameters=TaskParameters(amount=10000, recipient="Tolu"),
                risk="MONEY_MOVE",
            ),
        ],
    )
    state = OrchestratorState(
        user_id="u_dep_fanout_3",
        phone_number="2348111111193",
        channel="whatsapp",
        last_message_text="send 10k to mum and tolu",
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _MockPlanner(planner_output), "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert set(updates["tasks"].keys()) == {"t1", "t2"}
    assert updates["tasks"]["t1"].payload.get("recipient_name") == "Mum"
    assert updates["tasks"]["t2"].payload.get("recipient_name") == "Tolu"
    assert updates["waves"] == [["t1", "t2"]]


@pytest.mark.asyncio
async def test_planner_reconciles_obvious_multi_transfer_recipient_drift() -> None:
    planner_output = PlannerOutput(
        primary_intent="transfer",
        is_complex=True,
        tasks=[
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 10k each to mum, tolu and doyin",
                parameters=TaskParameters(amount=10000, recipient="Mum"),
                risk="MONEY_MOVE",
            ),
            PlannedTask(
                task_id="t2",
                action="send_money",
                executor="transfer",
                instruction="Send 10k each to mum, tolu and doyin",
                parameters=TaskParameters(amount=10000, recipient="Mum Tolu"),
                risk="MONEY_MOVE",
            ),
            PlannedTask(
                task_id="t3",
                action="send_money",
                executor="transfer",
                instruction="Send 10k each to mum, tolu and doyin",
                parameters=TaskParameters(amount=10000, recipient="Doyin"),
                risk="MONEY_MOVE",
            ),
        ],
    )
    state = OrchestratorState(
        user_id="u_dep_reconcile_1",
        phone_number="2348111111194",
        channel="whatsapp",
        last_message_text="send 10k each to mum, tolu and doyin",
    )
    config: RunnableConfig = {
        "configurable": {"task_planner": _MockPlanner(planner_output), "redis_client": None},
        "recursion_limit": 50,
    }

    updates = await plan_tasks(state, config)

    assert set(updates["tasks"].keys()) == {"t1", "t2", "t3"}
    assert updates["tasks"]["t1"].payload.get("recipient_name") == "Mum"
    assert updates["tasks"]["t2"].payload.get("recipient_name") == "tolu"
    assert updates["tasks"]["t3"].payload.get("recipient_name") == "Doyin"
    assert updates["waves"] == [["t1", "t2", "t3"]]


@pytest.mark.asyncio
async def test_dependent_task_cancelled_when_dependency_failed() -> None:
    state = OrchestratorState(
        user_id="u_dep_2",
        phone_number="2348111111102",
        channel="whatsapp",
        waves=[["t3"]],
        tasks={
            "t1": TaskSpec(id="t1", type="transfer", stage=TaskStage.FAILED, payload={}),
            "t2": TaskSpec(id="t2", type="transfer", stage=TaskStage.COMPLETED, payload={}),
            "t3": TaskSpec(
                id="t3",
                type="account",
                depends_on=["t1", "t2"],
                stage=TaskStage.DRAFT,
                payload={"action": "check_balance"},
            ),
        },
    )
    config: RunnableConfig = {
        "configurable": {"services": {"account": _AccountWorker()}},
        "recursion_limit": 50,
    }

    updates = await advance_wave(state, config)

    assert state.tasks["t3"].stage == TaskStage.CANCELLED
    assert updates["current_wave_index"] == 1


@pytest.mark.asyncio
async def test_dependent_task_runs_after_dependencies_completed() -> None:
    state = OrchestratorState(
        user_id="u_dep_3",
        phone_number="2348111111103",
        channel="whatsapp",
        waves=[["t3"]],
        tasks={
            "t1": TaskSpec(id="t1", type="transfer", stage=TaskStage.COMPLETED, payload={}),
            "t2": TaskSpec(id="t2", type="transfer", stage=TaskStage.COMPLETED, payload={}),
            "t3": TaskSpec(
                id="t3",
                type="account",
                depends_on=["t1", "t2"],
                stage=TaskStage.DRAFT,
                payload={"action": "check_balance"},
            ),
        },
        loaded_context={
            "profile": {},
            "accounts": [
                {
                    "id": "acct-1",
                    "bank_name": "Test Bank",
                    "account_number": "0000000001",
                    "mandate_status": "ready",
                    "mandate_id": "m1",
                }
            ],
        },
    )
    config: RunnableConfig = {
        "configurable": {"services": {"account": _AccountWorker()}},
        "recursion_limit": 50,
    }

    updates = await advance_wave(state, config)

    assert state.tasks["t3"].stage == TaskStage.COMPLETED
    assert updates["current_wave_index"] == 1
