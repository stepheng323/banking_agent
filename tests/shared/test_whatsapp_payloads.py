from shared.clients.whatsapp.payloads import (
    build_button_message_payload,
    build_flow_message_payload,
    build_list_message_payload,
)


def test_button_payload_truncates_titles_and_adds_header_footer() -> None:
    payload = build_button_message_payload(
        to="2348000000000",
        body_text="Choose one",
        buttons=[{"id": "one", "title": "A very long button title"}],
        header="Header",
        footer="Footer",
    )

    interactive = payload["interactive"]
    assert payload["type"] == "interactive"
    assert interactive["type"] == "button"
    assert interactive["header"] == {"type": "text", "text": "Header"}
    assert interactive["footer"] == {"text": "Footer"}
    assert interactive["action"]["buttons"][0]["reply"] == {
        "id": "one",
        "title": "A very long button t",
    }


def test_list_payload_limits_rows_and_normalizes_empty_values() -> None:
    options = [{"id": "", "title": "", "description": "x" * 100}] + [
        {"id": str(idx), "title": f"Option {idx}"} for idx in range(2, 12)
    ]

    payload = build_list_message_payload(
        to="2348000000000",
        body_text="Pick",
        options=options,
        list_button_text="Choose from this long label",
    )

    action = payload["interactive"]["action"]
    rows = action["sections"][0]["rows"]
    assert action["button"] == "Choose from this lon"
    assert len(rows) == 10
    assert rows[0] == {"id": "1", "title": "Option 1", "description": "x" * 72}


def test_flow_payload_defaults_to_navigate_screen_payload() -> None:
    flow_payload = build_flow_message_payload(
        to="2348000000000",
        flow_id="flow-id",
        flow_config={
            "header": "Authorize",
            "text_body": "Enter PIN",
            "screen_name": "PIN_ENTRY",
            "flow_token": "token-1",
        },
    )

    params = flow_payload.parameters
    assert flow_payload.flow_action == "navigate"
    assert flow_payload.screen_name == "PIN_ENTRY"
    assert params["flow_action_payload"] == {"screen": "PIN_ENTRY"}
    assert flow_payload.payload["interactive"]["action"]["parameters"] is params
