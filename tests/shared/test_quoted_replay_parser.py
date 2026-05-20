import pytest
from pydantic import ValidationError

from shared.types.quoted_replay import QuotedReplayInterpretation


def test_quoted_replay_contract_accepts_execute_shape() -> None:
    parsed = QuotedReplayInterpretation.model_validate(
        {
            "decision": "execute",
            "confidence": 0.92,
            "detected_language": "English",
            "tasks": [
                {
                    "task_type": "transfer",
                    "payload": {"amount": 50000, "recipient_name": "Tolu"},
                }
            ],
            "clarify_message": None,
            "reason": "single_replay_execute",
        }
    )

    assert parsed.decision == "execute"
    assert parsed.tasks[0].task_type == "transfer"
    assert parsed.tasks[0].payload.amount == 50000
    assert parsed.tasks[0].payload.recipient_name == "Tolu"


def test_quoted_replay_contract_accepts_multi_task_execute_shape() -> None:
    parsed = QuotedReplayInterpretation.model_validate(
        {
            "decision": "execute",
            "confidence": 0.88,
            "detected_language": "Pidgin",
            "tasks": [
                {"task_type": "transfer", "payload": {"amount": 5000, "recipient_name": "Ada"}},
                {"task_type": "airtime", "payload": {"amount": 1000, "recipient_phone": "08010000000"}},
            ],
            "reason": "replay_plus_extra_action",
        }
    )

    assert parsed.decision == "execute"
    assert len(parsed.tasks) == 2
    assert parsed.tasks[1].task_type == "airtime"


def test_quoted_replay_contract_accepts_replay_scope_fields() -> None:
    parsed = QuotedReplayInterpretation.model_validate(
        {
            "decision": "execute",
            "confidence": 0.91,
            "target_statuses": ["failed"],
            "target_types": ["airtime"],
            "target_task_ids": ["t_airtime"],
            "tasks": [],
            "reason": "retry_failed_airtime",
        }
    )

    assert parsed.target_statuses == ["failed"]
    assert parsed.target_types == ["airtime"]
    assert parsed.target_task_ids == ["t_airtime"]


def test_quoted_replay_contract_accepts_clarify_shape() -> None:
    parsed = QuotedReplayInterpretation.model_validate(
        {
            "decision": "clarify",
            "confidence": 0.4,
            "detected_language": "French",
            "tasks": [],
            "clarify_message": "Do you want to replace the recipient or add another transfer?",
            "reason": "ambiguous_add_or_replace",
        }
    )

    assert parsed.decision == "clarify"
    assert parsed.clarify_message is not None


def test_quoted_replay_contract_rejects_unknown_decision() -> None:
    with pytest.raises(ValidationError):
        QuotedReplayInterpretation.model_validate({"decision": "unknown"})


def test_quoted_replay_contract_rejects_unknown_payload_fields() -> None:
    with pytest.raises(ValidationError):
        QuotedReplayInterpretation.model_validate(
            {
                "decision": "execute",
                "confidence": 0.9,
                "tasks": [
                    {
                        "task_type": "transfer",
                        "payload": {
                            "amount": 5000,
                            "recipient_name": "Tolu",
                            "unexpected": "value",
                        },
                    }
                ],
            }
        )
