"""Tests for planner response_key typing contract."""

import pytest
from pydantic import ValidationError

from shared.types.planner import InterruptRouteDecision, PlannerOutput


def test_planner_output_accepts_allowed_response_key() -> None:
    payload = PlannerOutput(
        primary_intent="conversational",
        response_key="conversational.checkin",
        detected_language="Pidgin",
        tasks=[],
    )
    assert payload.response_key == "conversational.checkin"


def test_planner_output_accepts_identity_response_key() -> None:
    payload = PlannerOutput(
        primary_intent="conversational",
        response_key="conversational.identity",
        detected_language="English",
        tasks=[],
    )
    assert payload.response_key == "conversational.identity"


def test_planner_output_accepts_casual_chat_response_key() -> None:
    payload = PlannerOutput(
        primary_intent="conversational",
        response_key="conversational.casual_chat",
        detected_language="English",
        tasks=[],
    )
    assert payload.response_key == "conversational.casual_chat"


def test_planner_output_rejects_unknown_response_key() -> None:
    with pytest.raises(ValidationError):
        PlannerOutput.model_validate(
            {
                "primary_intent": "conversational",
                "response_key": "conversational.random",
                "detected_language": "English",
                "tasks": [],
            }
        )


def test_interrupt_route_decision_accepts_allowed_value() -> None:
    decision = InterruptRouteDecision(
        decision="switch_intent",
        confidence=0.92,
        detected_language="Pidgin",
        target_intent="beneficiary",
        reason="Different intent request.",
    )
    assert decision.decision == "switch_intent"


def test_interrupt_route_decision_accepts_status_query_value() -> None:
    decision = InterruptRouteDecision(
        decision="status_query",
        confidence=0.88,
        detected_language="Yoruba",
        target_intent=None,
        target_mode=None,
        status_query_type="requirements",
        reason="user asked what is required next",
    )
    assert decision.decision == "status_query"
    assert decision.status_query_type == "requirements"


def test_interrupt_route_decision_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        InterruptRouteDecision.model_validate(
            {
                "decision": "switch_now",
                "confidence": 0.5,
                "detected_language": "English",
                "target_intent": "account",
            }
        )
