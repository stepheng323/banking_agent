from __future__ import annotations

from typing import Any

import pytest

from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner import TaskPlanner
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_prompt_models import PlannerPromptSignals
from shared.types.planner import (
    AirtimeTaskParameters,
    DataTaskParameters,
    PlannerOutput,
    RecipientAllocation,
    TransferTaskParameters,
    make_planned_task,
)


class _StructuredResponder:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    async def ainvoke(self, _messages: list[dict[str, str]]) -> Any:
        return self.payload


class _FakeLLM:
    def __init__(self, planner_output: PlannerOutput) -> None:
        self._planner_output = planner_output

    def with_structured_output(
        self,
        schema: Any,
        *,
        method: str | None = None,
    ) -> _StructuredResponder:
        del method
        if isinstance(schema, type) and issubclass(schema, PlannerOutput):
            return _StructuredResponder(self._planner_output.model_dump(mode="python"))
        # The production planner now requests a compact, executor-scoped LLM
        # contract and adapts it back to PlannerOutput.  Keep this integration
        # fixture representative of that provider boundary rather than relying
        # on the retired broad PlannerOutput schema.
        raw = self._planner_output.model_dump(mode="python", exclude_none=True, exclude_defaults=True)
        include_executor = schema.__name__ in {
            "PlannerKnownTransferAirtimePlan",
            "PlannerKnownTransferDataPlan",
            "PlannerKnownAirtimeDataPlan",
            "PlannerKnownTransactionsPlan",
        }
        task_fields = {"task_id", "instruction", "source_clause_index", "action", "parameters"}
        if include_executor:
            task_fields.add("executor")
        return _StructuredResponder(
            {
                "primary_intent": raw["primary_intent"],
                "language": raw.get("detected_language"),
                "normalized_instruction": raw.get("normalized_instruction", ""),
                "clauses": [
                    {"task_ids": clause.get("task_ids", [])}
                    for clause in raw.get("clauses", [])
                ],
                "tasks": [
                    {
                        **{
                            key: value
                            for key, value in task.items()
                            if key in task_fields
                        },
                        **(
                            {
                                "executor": (
                                    "transfer"
                                    if str(task.get("action", "")).endswith("transfer")
                                    or task.get("action") == "send_money"
                                    else "airtime"
                                    if "airtime" in str(task.get("action", ""))
                                    else "data"
                                )
                            }
                            if include_executor
                            else {}
                        ),
                    }
                    for task in raw.get("tasks", [])
                ],
            }
        )


async def _plan(
    planner: TaskPlanner,
    phone_number: str,
    text: str,
    *,
    context: str = "None",
    prompt_signals: PlannerPromptSignals,
) -> PlannerOutput:
    result = await planner.plan_tasks_with_quality(
        phone_number,
        text,
        context=context,
        prompt_signals=prompt_signals,
    )
    return result.planner_output


def test_task_planner_exposes_quality_only_planning_api() -> None:
    assert hasattr(TaskPlanner, "plan_tasks_with_quality")
    assert not hasattr(TaskPlanner, "plan_tasks")


@pytest.mark.asyncio
async def test_task_planner_one_shot_transfer_is_normalized_before_return() -> None:
    planner_output = PlannerOutput(
        primary_intent="transfer",
        detected_language="English",
        tasks=[
            make_planned_task(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 20k to 0760505261 First Bank",
                parameters=TransferTaskParameters(recipient_name="Mum"),
                risk="MONEY_MOVE",
            )
        ],
    )
    planner = TaskPlanner(planner_llm=_FakeLLM(planner_output))

    result = await _plan(
        planner,
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
            make_planned_task(
                task_id="a1",
                action="buy_airtime",
                executor="airtime",
                instruction="Abeg buy 2k airtime for 08031234567 mtn",
                parameters=AirtimeTaskParameters(),
                risk="MONEY_MOVE",
            )
        ],
    )
    planner = TaskPlanner(planner_llm=_FakeLLM(planner_output))

    result = await _plan(
        planner,
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
            make_planned_task(
                task_id="d1",
                action="buy_data",
                executor="data",
                instruction="Jowo ra data 1gb fun 08031234567 mtn",
                parameters=DataTaskParameters(),
                risk="MONEY_MOVE",
            )
        ],
    )
    planner = TaskPlanner(planner_llm=_FakeLLM(planner_output))

    result = await _plan(
        planner,
        "2348000001002",
        "Jowo ra data 1gb fun 08031234567 mtn",
        context="None",
        prompt_signals=PlannerPromptSignals(has_transaction_intent_hint=True),
    )

    params = result.tasks[0].parameters
    assert params.recipient_phone == "08031234567"
    assert params.network == "MTN"
    assert params.plan == "1GB"


@pytest.mark.asyncio
async def test_task_planner_preserves_three_way_recipient_allocations() -> None:
    planner_output = PlannerOutput(
        primary_intent="transfer",
        detected_language="English",
        tasks=[
            make_planned_task(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 10k each to Mum, Tolu and Doyin",
                parameters=TransferTaskParameters(
                    amount=30000,
                    recipient_allocations=[
                        RecipientAllocation(recipient_name="Mum", amount=10000),
                        RecipientAllocation(recipient_name="Tolu", amount=10000),
                        RecipientAllocation(recipient_name="Doyin", amount=10000),
                    ],
                ),
                risk="MONEY_MOVE",
            )
        ],
    )
    planner = TaskPlanner(planner_llm=_FakeLLM(planner_output))

    result = await _plan(
        planner,
        "2348000001003",
        "Send 10k each to Mum, Tolu and Doyin",
        context="None",
        prompt_signals=PlannerPromptSignals(has_transaction_intent_hint=True),
    )

    allocations = result.tasks[0].parameters.recipient_allocations
    assert allocations is not None
    assert [item.recipient_name for item in allocations] == ["Mum", "Tolu", "Doyin"]
    assert [item.amount for item in allocations] == [10000, 10000, 10000]


@pytest.mark.asyncio
async def test_task_planner_collapses_bulk_transfer_siblings_to_recipient_allocations() -> None:
    planner_output = PlannerOutput(
        primary_intent="mixed",
        detected_language="English",
        tasks=[
            make_planned_task(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to Mum",
                parameters=TransferTaskParameters(amount=10000, recipient_name="Mum"),
                risk="MONEY_MOVE",
            ),
            make_planned_task(
                task_id="t2",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to Tolu",
                parameters=TransferTaskParameters(amount=10000, recipient_name="Tolu"),
                risk="MONEY_MOVE",
            ),
            make_planned_task(
                task_id="t3",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to Doyin",
                parameters=TransferTaskParameters(amount=10000, recipient_name="Doyin"),
                risk="MONEY_MOVE",
            ),
        ],
    )
    planner = TaskPlanner(planner_llm=_FakeLLM(planner_output))

    result = await _plan(
        planner,
        "2348000001004",
        "Send 10k each to Mum, Tolu and Doyin",
        context="None",
        prompt_signals=PlannerPromptSignals(has_transaction_intent_hint=True),
    )

    assert len(result.tasks) == 1
    allocations = result.tasks[0].parameters.recipient_allocations
    assert allocations is not None
    assert result.tasks[0].parameters.amount == 30000
    assert [item.recipient_name for item in allocations] == ["Mum", "Tolu", "Doyin"]
    assert [item.amount for item in allocations] == [10000, 10000, 10000]


@pytest.mark.asyncio
async def test_task_planner_collapses_bulk_transfer_siblings_with_singleton_allocations() -> None:
    planner_output = PlannerOutput(
        primary_intent="mixed",
        detected_language="English",
        tasks=[
            make_planned_task(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to Mum",
                parameters=TransferTaskParameters(
                    amount=10000,
                    recipient_name="Mum",
                    recipient_allocations=[RecipientAllocation(recipient_name="Mum", amount=10000)],
                ),
                risk="MONEY_MOVE",
            ),
            make_planned_task(
                task_id="t2",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to Tolu Adedayo",
                parameters=TransferTaskParameters(
                    amount=10000,
                    recipient_name="Tolu Adedayo",
                    recipient_allocations=[RecipientAllocation(recipient_name="Tolu Adedayo", amount=10000)],
                ),
                risk="MONEY_MOVE",
            ),
            make_planned_task(
                task_id="t3",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to Doyin",
                parameters=TransferTaskParameters(
                    amount=10000,
                    recipient_name="Doyin",
                    recipient_allocations=[RecipientAllocation(recipient_name="Doyin", amount=10000)],
                ),
                risk="MONEY_MOVE",
            ),
        ],
    )
    planner = TaskPlanner(planner_llm=_FakeLLM(planner_output))

    result = await _plan(
        planner,
        "2348000001005",
        "okay send 10k each to mum, tolu and doyin",
        context="None",
        prompt_signals=PlannerPromptSignals(has_transaction_intent_hint=True),
    )

    assert len(result.tasks) == 1
    allocations = result.tasks[0].parameters.recipient_allocations
    assert allocations is not None
    assert result.tasks[0].parameters.amount == 30000
    assert [item.recipient_name for item in allocations] == ["Mum", "Tolu Adedayo", "Doyin"]
    assert [item.amount for item in allocations] == [10000, 10000, 10000]
