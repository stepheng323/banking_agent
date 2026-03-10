"""Show-options intent mapping tests."""

from apps.core.src.agent.orchestrator.models.intents import ShowFlow, ShowOptions, reconstruct_intent
from apps.core.src.agent.orchestrator.presentation.intents import map_outbox_to_intents


def test_reconstruct_intent_handles_show_options() -> None:
    raw = {
        "type": "show_options",
        "title": "Pick one",
        "task_ids": ["t1"],
        "options": [{"id": "1", "title": "First"}],
    }

    intent = reconstruct_intent(raw)

    assert isinstance(intent, ShowOptions)
    assert intent.title == "Pick one"
    assert intent.task_ids == ["t1"]
    assert intent.options == [{"id": "1", "title": "First"}]


def test_map_outbox_to_intents_keeps_show_options_without_extra_say() -> None:
    outbox = [
        {
            "type": "show_options",
            "title": "Pick one",
            "task_ids": ["t1"],
            "options": [{"id": "1", "title": "First"}],
        }
    ]

    intents = map_outbox_to_intents(outbox, response_text="Fallback text")

    assert len(intents) == 1
    assert isinstance(intents[0], ShowOptions)


def test_map_outbox_to_intents_keeps_flow_without_extra_say() -> None:
    outbox = [
        {
            "type": "flow",
            "flow_id": "flow_123",
            "flow_config": {"header": "Link New Account"},
            "fallback_text": "Open the flow",
        }
    ]

    intents = map_outbox_to_intents(outbox, response_text="Fallback text")

    assert len(intents) == 1
    assert isinstance(intents[0], ShowFlow)
