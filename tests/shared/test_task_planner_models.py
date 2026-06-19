"""Tests for TaskPlanner model wiring."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from apps.chat.src.agent.orchestrator.workflows.gate.utils.semantic_router_llm import SemanticRouterLLM
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner import TaskPlanner
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_prompt_models import PlannerPromptSignals
from shared.config.settings import settings
from shared.types.planner import (
    AirtimeTaskParameters,
    DataTaskParameters,
    PlannerOutput,
    PlannerOutputTransferAirtime,
    PlannerOutputTransferData,
    PlannerOutputTransferOnly,
    QueryTaskParameters,
    ScheduleTaskParameters,
    TransferTaskParameters,
    coerce_task_parameters,
    copy_task_parameters,
    dump_task_parameters,
    make_planned_task,
    planner_output_model_for_transaction_executors,
    task_model_for_action,
    task_parameter_model_for_action,
    task_parameter_model_for_executor,
)
from shared.types.quoted_replay import QuotedReplayInterpretation


class _StructuredResponder:
    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.last_messages: list[dict[str, str]] | None = None

    async def ainvoke(self, messages: list[dict[str, str]]) -> Any:
        self.last_messages = messages
        return self.payload


class _FakeLLM:
    def __init__(self, name: str) -> None:
        self.name = name

    def with_structured_output(
        self,
        schema: Any,
        *,
        method: str | None = None,
    ) -> str:
        del method
        return f"{self.name}:{getattr(schema, '__name__', 'unknown')}"


class _StructuredFakeLLM:
    model_name = "structured-fake"

    def __init__(self, payloads: dict[str, Any]) -> None:
        self.payloads = payloads
        self.schema_names: list[str] = []

    def with_structured_output(self, schema: Any, *args: Any, **kwargs: Any) -> _StructuredResponder:
        del args, kwargs
        schema_name = getattr(schema, "__name__", "unknown")
        self.schema_names.append(schema_name)
        return _StructuredResponder(self.payloads.get(schema_name, {}))


def test_task_planner_uses_dedicated_interrupt_model_when_provided() -> None:
    planner_llm = _FakeLLM("planner")
    interrupt_llm = _FakeLLM("interrupt")

    planner = TaskPlanner(
        planner_llm=planner_llm,
        interrupt_llm=interrupt_llm,
    )

    assert planner.structured_planner == "planner:PlannerOutput"
    assert planner.structured_interrupt_router == "interrupt:InterruptRouteDecision"
    assert planner.structured_quoted_replay == "planner:QuotedReplayInterpretation"


def test_task_planner_falls_back_to_planner_model_for_interrupt_router() -> None:
    planner_llm = _FakeLLM("planner")

    planner = TaskPlanner(planner_llm=planner_llm)

    assert planner.structured_interrupt_router == "planner:InterruptRouteDecision"


def test_task_planner_falls_back_to_interrupt_model_for_semantic_router_when_not_provided() -> None:
    """SemanticRouterLLM is now a standalone class; this test verifies it uses the supplied LLM."""
    semantic_llm = _FakeLLM("semantic")
    router = SemanticRouterLLM(llm=semantic_llm)  # type: ignore[arg-type]
    # The router holds two structured-output handles, both using the semantic LLM
    assert router.structured_semantic_router == "semantic:SemanticRouteDecision"
    assert router.structured_schedule_read_router == "semantic:SemanticRouteDecision"


async def test_task_planner_route_semantic_turn_uses_shared_structured_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "llm_response_cache_enabled", False)
    router_llm = _StructuredFakeLLM(
        {
            "SemanticRouteDecision": {
                "decision": "domain_query",
                "confidence": 0.91,
                "detected_language": "en",
                "target_intent": "query",
                "reason": "query_followup",
            }
        }
    )
    router = SemanticRouterLLM(llm=router_llm)  # type: ignore[arg-type]

    decision = await router.route_semantic_turn(
        "2348000000010",
        "what about last week",
        context="Recent query result",
    )

    assert decision.decision == "domain_query"
    assert decision.target_intent == "query"
    assert router.structured_semantic_router.last_messages is not None
    assert router.structured_semantic_router.last_messages[1]["content"].startswith("User phone: 2348000000010")


async def test_task_planner_pending_action_edit_uses_shared_structured_invocation() -> None:
    planner_llm = _StructuredFakeLLM(
        {
            "PendingActionEditDecision": {
                "operation": "update_fields",
                "confidence": 0.88,
                "amount": 20000,
                "reason": "amount_update",
            }
        }
    )
    planner = TaskPlanner(planner_llm=planner_llm)

    decision = await planner.interpret_pending_action_edit(
        "2348000000011",
        "make it 20k",
        context="Pending transfer confirmation",
    )

    assert decision.operation == "update_fields"
    assert decision.amount == 20000
    assert planner.structured_pending_action_edit.last_messages is not None
    assert "Pending transfer confirmation" in planner.structured_pending_action_edit.last_messages[1]["content"]


async def test_task_planner_quoted_replay_uses_shared_structured_invocation() -> None:
    planner_llm = _StructuredFakeLLM(
        {
            "QuotedReplayInterpretation": {
                "decision": "execute",
                "confidence": 0.86,
                "detected_language": "en",
                "tasks": [{"task_type": "transfer", "payload": {"amount": 2000, "recipient_name": "Mum"}}],
                "target_types": ["transfer"],
            }
        }
    )
    planner = TaskPlanner(planner_llm=planner_llm)

    decision = await planner.interpret_quoted_replay(
        "2348000000012",
        "send again",
        context="Quoted failed transfer receipt",
    )

    assert isinstance(decision, QuotedReplayInterpretation)
    assert decision.decision == "execute"
    assert decision.tasks[0].payload.amount == 2000
    assert planner.structured_quoted_replay.last_messages is not None
    assert "Quoted failed transfer receipt" in planner.structured_quoted_replay.last_messages[1]["content"]


async def test_task_planner_uses_narrow_transfer_output_schema_for_transfer_only_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "llm_response_cache_enabled", False)
    planner_llm = _StructuredFakeLLM(
        {
            "PlannerOutputTransferOnly": {
                "primary_intent": "transfer",
                "confidence": 0.96,
                "tasks": [
                    {
                        "task_id": "t1",
                        "action": "send_money",
                        "instruction": "Send money",
                        "risk": "MONEY_MOVE",
                        "parameters": {"amount": "2000", "recipient_name": "Mum"},
                    }
                ],
            }
        }
    )
    planner = TaskPlanner(planner_llm=planner_llm)

    result = await planner.plan_tasks_with_quality(
        "2348000000013",
        "Send 2k to Mum",
        prompt_signals=PlannerPromptSignals(
            forced_domain_owner="transfer",
            expected_transaction_executors=("transfer",),
            compact_context=True,
        ),
    )

    assert result.raw_output.tasks[0].action == "send_money"
    assert result.raw_output.tasks[0].executor == "transfer"
    assert "PlannerOutputTransferOnly" in planner_llm.schema_names


def test_planner_task_parameters_are_coerced_by_executor_and_action() -> None:
    output = PlannerOutput(
        primary_intent="mixed",
        tasks=[
            make_planned_task(
                task_id="t1",
                executor="transfer",
                action="send_money",
                instruction="Send money",
                parameters={"amount": 2000, "recipient_name": "Mum"},
                risk="MONEY_MOVE",
            ),
            make_planned_task(
                task_id="a1",
                executor="airtime",
                action="buy_airtime",
                instruction="Buy airtime",
                parameters={"amount": 1000, "network": "MTN", "is_self": True},
                risk="MONEY_MOVE",
            ),
            make_planned_task(
                task_id="d1",
                executor="data",
                action="buy_data",
                instruction="Buy data",
                parameters={"plan": "1GB", "network": "MTN", "is_self": True},
                risk="MONEY_MOVE",
            ),
            make_planned_task(
                task_id="s1",
                executor="schedule",
                action="list_scheduled_transactions",
                instruction="List scheduled transfers",
                parameters={"schedule_response_mode": "count"},
                risk="READ_ONLY",
            ),
            make_planned_task(
                task_id="q1",
                executor="query",
                action="transaction_search",
                instruction="Show transactions",
                parameters={"response_shape": "surface_paginated"},
                risk="READ_ONLY",
            ),
        ],
    )

    assert isinstance(output.tasks[0].parameters, TransferTaskParameters)
    assert isinstance(output.tasks[1].parameters, AirtimeTaskParameters)
    assert isinstance(output.tasks[2].parameters, DataTaskParameters)
    assert isinstance(output.tasks[3].parameters, ScheduleTaskParameters)
    assert isinstance(output.tasks[4].parameters, QueryTaskParameters)
    assert output.model_dump()["tasks"][0]["parameters"]["recipient_name"] == "Mum"


def test_task_parameter_model_helpers_use_executor_specific_contracts() -> None:
    assert task_parameter_model_for_executor("transfer", "send_money") is TransferTaskParameters
    assert task_parameter_model_for_executor("airtime", "buy_airtime") is AirtimeTaskParameters
    assert task_parameter_model_for_executor("data", "buy_data") is DataTaskParameters
    assert task_parameter_model_for_executor("schedule", "list_scheduled_transactions") is ScheduleTaskParameters
    assert task_parameter_model_for_action("list_scheduled_transactions") is ScheduleTaskParameters
    assert task_model_for_action("send_money").model_fields["parameters"].annotation is TransferTaskParameters

    data_params = coerce_task_parameters("data", "buy_data", {"plan": "1GB", "network": "MTN"})
    copied_params = copy_task_parameters(data_params)

    assert isinstance(data_params, DataTaskParameters)
    assert isinstance(copied_params, DataTaskParameters)
    assert dump_task_parameters(data_params) == {"is_self": False, "network": "MTN", "plan": "1GB"}


def test_wrong_executor_parameter_combination_fails_validation() -> None:
    with pytest.raises(ValidationError):
        make_planned_task(
            task_id="t1",
            executor="transfer",
            action="send_money",
            instruction="Send money",
            parameters={"amount": 2000, "network": "MTN"},
            risk="MONEY_MOVE",
        )


def test_planner_output_schema_uses_executor_specific_parameter_union() -> None:
    schema = PlannerOutput.model_json_schema()
    schema_size = len(json.dumps(schema))
    task_items = schema["properties"]["tasks"]["items"]
    action_mapping = task_items["discriminator"]["mapping"]

    assert schema["title"] == "PlannerOutput"
    assert task_items["discriminator"]["propertyName"] == "action"
    assert action_mapping["send_money"] == "#/$defs/TransferPlannedTask"
    assert action_mapping["buy_airtime"] == "#/$defs/AirtimePlannedTask"
    assert action_mapping["buy_data"] == "#/$defs/DataPlannedTask"
    assert action_mapping["list_scheduled_transactions"] == "#/$defs/SchedulePlannedTask"
    assert len(task_items["oneOf"]) == 10
    task = make_planned_task(
        task_id="t1",
        executor="transfer",
        action="send_money",
        instruction="Send money",
        parameters={"amount": 2000, "recipient_name": "Mum"},
        risk="MONEY_MOVE",
    )
    assert "executor" not in schema["$defs"]["TransferPlannedTask"]["properties"]
    assert task.executor == "transfer"
    assert task.model_dump().get("executor") is None
    assert "network" not in schema["$defs"]["TransferTaskParameters"]["properties"]
    assert "recipient_account" not in schema["$defs"]["AirtimeTaskParameters"]["properties"]
    assert schema_size < 25000


def test_narrow_planner_output_schemas_limit_llm_facing_task_contracts() -> None:
    base_size = len(json.dumps(PlannerOutput.model_json_schema(), separators=(",", ":"), sort_keys=True))
    transfer_size = len(
        json.dumps(PlannerOutputTransferOnly.model_json_schema(), separators=(",", ":"), sort_keys=True)
    )
    transfer_airtime_size = len(
        json.dumps(PlannerOutputTransferAirtime.model_json_schema(), separators=(",", ":"), sort_keys=True)
    )
    transfer_data_size = len(
        json.dumps(PlannerOutputTransferData.model_json_schema(), separators=(",", ":"), sort_keys=True)
    )

    assert planner_output_model_for_transaction_executors(("transfer",)) is PlannerOutputTransferOnly
    assert planner_output_model_for_transaction_executors(("transfer", "airtime")) is PlannerOutputTransferAirtime
    assert planner_output_model_for_transaction_executors(("transfer", "data")) is PlannerOutputTransferData
    assert transfer_size < base_size * 0.4
    assert transfer_airtime_size < base_size * 0.6
    assert transfer_data_size < base_size * 0.6

    transfer_schema_text = json.dumps(PlannerOutputTransferOnly.model_json_schema())
    assert "AirtimeTaskParameters" not in transfer_schema_text
    assert "DataTaskParameters" not in transfer_schema_text

    with pytest.raises(ValidationError):
        PlannerOutputTransferOnly.model_validate(
            {
                "primary_intent": "airtime",
                "tasks": [
                    {
                        "task_id": "a1",
                        "action": "buy_airtime",
                        "instruction": "Buy airtime",
                        "risk": "MONEY_MOVE",
                        "parameters": {"amount": "1000", "is_self": True},
                    }
                ],
            }
        )


def test_old_universal_task_parameters_constructor_is_not_reintroduced() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    forbidden_constructor = re.compile(r"(?<![A-Za-z])" + "TaskParameters" + r"\(")
    offenders: list[str] = []

    for directory_name in ("apps", "banking", "scripts", "shared", "tests"):
        for path in (repo_root / directory_name).rglob("*.py"):
            text = path.read_text()
            if forbidden_constructor.search(text):
                offenders.append(str(path.relative_to(repo_root)))

    assert offenders == []
