"""Tests for TaskPlanner model wiring."""

from __future__ import annotations

from typing import Any

from apps.chat.src.agent.orchestrator.planning.task_planner import TaskPlanner
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

    def with_structured_output(self, schema: Any, *args: Any, **kwargs: Any) -> _StructuredResponder:
        del args, kwargs
        schema_name = getattr(schema, "__name__", "unknown")
        return _StructuredResponder(self.payloads.get(schema_name, {}))


def test_task_planner_uses_dedicated_interrupt_model_when_provided() -> None:
    planner_llm = _FakeLLM("planner")
    interrupt_llm = _FakeLLM("interrupt")
    semantic_router_llm = _FakeLLM("semantic")

    planner = TaskPlanner(
        planner_llm=planner_llm,
        semantic_router_llm=semantic_router_llm,
        interrupt_llm=interrupt_llm,
    )

    assert planner.structured_planner == "planner:PlannerOutput"
    assert planner.structured_semantic_router == "semantic:SemanticRouteDecision"
    assert planner.structured_interrupt_router == "interrupt:InterruptRouteDecision"
    assert planner.structured_quoted_replay == "planner:QuotedReplayInterpretation"


def test_task_planner_falls_back_to_planner_model_for_interrupt_router() -> None:
    planner_llm = _FakeLLM("planner")

    planner = TaskPlanner(planner_llm=planner_llm)

    assert planner.structured_semantic_router == "planner:SemanticRouteDecision"
    assert planner.structured_interrupt_router == "planner:InterruptRouteDecision"


def test_task_planner_falls_back_to_interrupt_model_for_semantic_router_when_not_provided() -> None:
    planner_llm = _FakeLLM("planner")
    interrupt_llm = _FakeLLM("interrupt")

    planner = TaskPlanner(planner_llm=planner_llm, interrupt_llm=interrupt_llm)

    assert planner.structured_semantic_router == "interrupt:SemanticRouteDecision"
    assert planner.structured_interrupt_router == "interrupt:InterruptRouteDecision"


async def test_task_planner_route_semantic_turn_uses_shared_structured_invocation() -> None:
    planner_llm = _StructuredFakeLLM(
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
    planner = TaskPlanner(planner_llm=planner_llm)

    decision = await planner.route_semantic_turn(
        "2348000000010",
        "what about last week",
        context="Recent query result",
    )

    assert decision.decision == "domain_query"
    assert decision.target_intent == "query"
    assert planner.structured_semantic_router.last_messages is not None
    assert planner.structured_semantic_router.last_messages[1]["content"].startswith("User phone: 2348000000010")


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
