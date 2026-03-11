from __future__ import annotations

from typing import Any

import pytest

from shared.services.task_planner import TaskPlanner
from shared.services.task_planner_prompt_models import PlannerPromptSignals
from shared.types.planner import PlannedTask, PlannerOutput, TaskParameters


class _StructuredResponder:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    async def ainvoke(self, _messages: list[dict[str, str]]) -> Any:
        return self.payload


class _FakeLLM:
    def __init__(self, planner_output: PlannerOutput) -> None:
        self._planner_output = planner_output

    def with_structured_output(self, schema: Any) -> _StructuredResponder:
        schema_name = getattr(schema, "__name__", "")
        if schema_name == "PlannerOutput":
            return _StructuredResponder(self._planner_output)
        return _StructuredResponder({})


@pytest.mark.asyncio
async def test_task_planner_one_shot_transfer_is_normalized_before_return() -> None:
    planner_output = PlannerOutput(
        primary_intent="transfer",
        detected_language="English",
        tasks=[
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 20k to 0760505261 First Bank",
                parameters=TaskParameters(recipient_name="Mum"),
                risk="MONEY_MOVE",
            )
        ],
    )
    planner = TaskPlanner(planner_llm=_FakeLLM(planner_output))

    result = await planner.plan_tasks(
        "2348000001000",
        "Send 20k to 0760505261 First Bank",
        context="None",
        prompt_signals=PlannerPromptSignals(has_transaction_intent_hint=True),
    )

    params = result.tasks[0].parameters
    assert params.amount == 20000
    assert params.recipient_account == "0760505261"
    assert params.bank_name == "First Bank"


@pytest.mark.asyncio
async def test_task_planner_one_shot_airtime_is_normalized_before_return() -> None:
    planner_output = PlannerOutput(
        primary_intent="airtime",
        detected_language="Pidgin",
        tasks=[
            PlannedTask(
                task_id="a1",
                action="buy_airtime",
                executor="airtime",
                instruction="Abeg buy 2k airtime for 08031234567 mtn",
                parameters=TaskParameters(),
                risk="MONEY_MOVE",
            )
        ],
    )
    planner = TaskPlanner(planner_llm=_FakeLLM(planner_output))

    result = await planner.plan_tasks(
        "2348000001001",
        "Abeg buy 2k airtime for 08031234567 mtn",
        context="None",
        prompt_signals=PlannerPromptSignals(has_transaction_intent_hint=True),
    )

    params = result.tasks[0].parameters
    assert params.amount == 2000
    assert params.recipient_phone == "08031234567"
    assert params.network == "MTN"


@pytest.mark.asyncio
async def test_task_planner_one_shot_data_is_normalized_before_return() -> None:
    planner_output = PlannerOutput(
        primary_intent="data",
        detected_language="Yoruba",
        tasks=[
            PlannedTask(
                task_id="d1",
                action="buy_data",
                executor="data",
                instruction="Jowo ra data 1gb fun 08031234567 mtn",
                parameters=TaskParameters(),
                risk="MONEY_MOVE",
            )
        ],
    )
    planner = TaskPlanner(planner_llm=_FakeLLM(planner_output))

    result = await planner.plan_tasks(
        "2348000001002",
        "Jowo ra data 1gb fun 08031234567 mtn",
        context="None",
        prompt_signals=PlannerPromptSignals(has_transaction_intent_hint=True),
    )

    params = result.tasks[0].parameters
    assert params.recipient_phone == "08031234567"
    assert params.network == "MTN"
    assert params.plan == "1GB"
