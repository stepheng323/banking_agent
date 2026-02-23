"""Contract tests for planner prompt guidance."""

from shared.services.task_planner import BASE_PLANNER_SYSTEM_PROMPT


def test_beneficiary_reactive_save_requires_explicit_intent() -> None:
    """Prompt should prevent greeting text from being treated as save consent."""
    assert "BENEFICIARY SAVING (Reactive)" in BASE_PLANNER_SYSTEM_PROMPT
    assert "Treat greetings/check-ins/thanks" in BASE_PLANNER_SYSTEM_PROMPT
    expected = 'Context="Asked to save beneficiary", User="Hi" -> conversational, response_key=conversational.greeting'
    assert expected in BASE_PLANNER_SYSTEM_PROMPT
