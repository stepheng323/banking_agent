"""End-to-end-ish beneficiary option selection tests across channels."""

from types import SimpleNamespace
from uuid import uuid4

from apps.chat.src.agent.graphs.transfer.models.types import TransferContext, TransferGates, TransferPayload
from apps.chat.src.agent.graphs.transfer.nodes.extraction import ExtractionStep
from apps.gateway.adapters.meta_whatsapp import parse_payload
from apps.gateway.adapters.telegram import parse_update


def _beneficiaries() -> tuple[list[dict], str, str]:
    first_id = str(uuid4())
    second_id = str(uuid4())
    return (
        [
            {
                "id": first_id,
                "user_id": str(uuid4()),
                "account_name": "John Doe",
                "alias": "John D",
                "account_number": "0011223344",
                "bank_code": "044",
                "bank_name": "Access Bank",
            },
            {
                "id": second_id,
                "user_id": str(uuid4()),
                "account_name": "John Smith",
                "alias": "John S",
                "account_number": "9988776655",
                "bank_code": "058",
                "bank_name": "GTBank",
            },
        ],
        first_id,
        second_id,
    )


async def _resolve_selected_beneficiary(user_message: str, beneficiaries: list[dict]) -> dict:
    step = ExtractionStep(user_message=user_message)
    payload = TransferPayload(recipient_name="john")
    context = TransferContext(
        phone_number="2348000000999",
        language="en",
        beneficiaries=beneficiaries,
        accounts=[],
    )
    worker_context = SimpleNamespace(
        extractor=None,
        required_fields=["beneficiary_id"],
        previous_response="Which recipient did you mean?",
    )
    result = await step.execute(payload, context, TransferGates(), worker_context)
    return result.patch


async def test_whatsapp_button_tap_resolves_beneficiary_selection() -> None:
    beneficiaries, first_id, second_id = _beneficiaries()
    del first_id
    webhook = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid-1",
                                    "from": "2348000000999",
                                    "type": "interactive",
                                    "interactive": {
                                        "type": "button_reply",
                                        "button_reply": {"id": "2", "title": "John Smith"},
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }

    parsed = parse_payload(webhook)
    assert parsed and parsed[0].text == "2"

    patch = await _resolve_selected_beneficiary(parsed[0].text, beneficiaries)
    assert patch["beneficiary_id"] == second_id


async def test_whatsapp_list_tap_resolves_beneficiary_selection() -> None:
    beneficiaries, first_id, second_id = _beneficiaries()
    del first_id
    webhook = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid-2",
                                    "from": "2348000000999",
                                    "type": "interactive",
                                    "interactive": {
                                        "type": "list_reply",
                                        "list_reply": {"id": "2", "title": "John Smith"},
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }

    parsed = parse_payload(webhook)
    assert parsed and parsed[0].text == "2"

    patch = await _resolve_selected_beneficiary(parsed[0].text, beneficiaries)
    assert patch["beneficiary_id"] == second_id


async def test_telegram_callback_tap_resolves_beneficiary_selection() -> None:
    beneficiaries, first_id, second_id = _beneficiaries()
    del second_id
    update = {
        "callback_query": {
            "id": "cbq-1",
            "data": "1",
            "message": {"message_id": 45, "chat": {"id": 12345678}},
            "from": {"id": 111},
        }
    }

    parsed = parse_update(update)
    assert parsed is not None
    assert parsed.type == "callback_query"
    assert parsed.text == "1"

    patch = await _resolve_selected_beneficiary(parsed.text, beneficiaries)
    assert patch["beneficiary_id"] == first_id


async def test_typed_number_fallback_still_resolves_beneficiary_selection() -> None:
    beneficiaries, first_id, second_id = _beneficiaries()
    del first_id
    update = {
        "message": {
            "message_id": 46,
            "chat": {"id": 12345678},
            "from": {"id": 111},
            "text": "2",
        }
    }

    parsed = parse_update(update)
    assert parsed is not None
    assert parsed.type == "text"
    assert parsed.text == "2"

    patch = await _resolve_selected_beneficiary(parsed.text, beneficiaries)
    assert patch["beneficiary_id"] == second_id


async def test_typed_option_id_fallback_resolves_beneficiary_selection() -> None:
    beneficiaries, first_id, second_id = _beneficiaries()
    del first_id
    option_id = f"bene:{second_id}"
    update = {
        "message": {
            "message_id": 47,
            "chat": {"id": 12345678},
            "from": {"id": 111},
            "text": option_id,
        }
    }

    parsed = parse_update(update)
    assert parsed is not None
    assert parsed.type == "text"
    assert parsed.text == option_id

    patch = await _resolve_selected_beneficiary(parsed.text, beneficiaries)
    assert patch["beneficiary_id"] == second_id
