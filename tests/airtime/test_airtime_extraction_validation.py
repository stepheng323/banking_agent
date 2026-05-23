from types import SimpleNamespace

import pytest

from apps.chat.src.agent.graphs.airtime.models.types import AirtimeContext, AirtimeGates, AirtimePayload
from apps.chat.src.agent.graphs.airtime.nodes.extraction import ExtractionStep
from apps.chat.src.agent.graphs.airtime.nodes.selection import SourceSelectionStep
from apps.chat.src.agent.graphs.airtime.nodes.validation import ValidationStep
from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome


class _ExtractorStub:
    def __init__(self, result: dict) -> None:
        self._result = result
        self.calls = 0
        self.last_state: dict | None = None

    async def run(self, _state: dict) -> dict:
        self.calls += 1
        self.last_state = _state
        return self._result


@pytest.mark.asyncio
async def test_airtime_extraction_applies_normalized_network_patch() -> None:
    step = ExtractionStep("buy 5k airtime")
    payload = AirtimePayload(amount=5000, recipient_phone="08162511023")
    context = AirtimeContext(phone_number="2348000000000", language="en")
    gates = AirtimeGates()
    worker_context = SimpleNamespace(
        required_fields=[],
        extractor=_ExtractorStub({"entities": {"network": "Airtel"}, "correction": None}),
    )

    result = await step.execute(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch == {"network": "AIRTEL"}


@pytest.mark.asyncio
async def test_airtime_extraction_applies_network_correction_patch() -> None:
    step = ExtractionStep("no, make am mtn")
    payload = AirtimePayload(amount=5000, recipient_phone="08162511023", network="AIRTEL")
    context = AirtimeContext(phone_number="2348000000000", language="en")
    gates = AirtimeGates()
    worker_context = SimpleNamespace(
        required_fields=["network"],
        extractor=_ExtractorStub(
            {"entities": {}, "correction": {"field": "network", "new_value": "mtn"}},
        ),
    )

    result = await step.execute(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch == {"network": "MTN"}


@pytest.mark.asyncio
async def test_airtime_extraction_applies_source_bank_name_patch() -> None:
    step = ExtractionStep("buy me 2k airtime from my gtb")
    payload = AirtimePayload(amount=2000)
    context = AirtimeContext(phone_number="2348000000000", language="en")
    gates = AirtimeGates()
    worker_context = SimpleNamespace(
        required_fields=[],
        extractor=_ExtractorStub({"entities": {"source_bank_name": "GTB"}, "correction": None}),
    )

    result = await step.execute(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch == {"source_bank_name": "GTB", "is_self": True}


@pytest.mark.asyncio
async def test_airtime_extraction_source_bank_fallback_uses_linked_accounts() -> None:
    step = ExtractionStep("buy me 2k airtime from my gtb")
    payload = AirtimePayload(amount=2000)
    context = AirtimeContext(
        phone_number="2348000000000",
        language="en",
        accounts=[
            {"bank_name": "Access Bank", "account_number": "0000000003"},
            {"bank_name": "GTBank", "account_number": "0000000002"},
        ],
    )
    gates = AirtimeGates()
    worker_context = SimpleNamespace(
        required_fields=[],
        extractor=_ExtractorStub({"entities": {}, "correction": None}),
    )

    result = await step.execute(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch == {"is_self": True, "source_bank_name": "GTBank"}


@pytest.mark.asyncio
async def test_airtime_source_selection_honors_explicit_bank_over_default() -> None:
    step = SourceSelectionStep()
    payload = AirtimePayload(amount=2000, recipient_phone="08162511023", network="MTN", source_bank_name="GTB")
    context = AirtimeContext(
        phone_number="2348000000000",
        language="en",
        accounts=[
            {
                "id": "access-1",
                "bank_name": "Access Bank",
                "account_name": "Access Main",
                "account_number": "0000000003",
                "is_default": True,
            },
            {
                "id": "gtb-1",
                "bank_name": "GTBank",
                "account_name": "GT Main",
                "account_number": "0000000002",
                "is_default": False,
            },
        ],
    )

    result = await step.execute(payload, context, AirtimeGates(), SimpleNamespace())

    assert result.outcome == TransactionOutcome.OK
    assert result.patch == {
        "source_account_id": "gtb-1",
        "source_bank_name": "GTBank",
        "source_account_name": "GT Main",
        "source_account_number": "0000000002",
    }


@pytest.mark.asyncio
async def test_airtime_extraction_phone_slot_fallback_normalizes_digits_only_reply() -> None:
    step = ExtractionStep("816 251 1023")
    payload = AirtimePayload(amount=5000)
    context = AirtimeContext(phone_number="2348000000000", language="en")
    gates = AirtimeGates()
    worker_context = SimpleNamespace(
        required_fields=["recipient_phone"],
        extractor=_ExtractorStub({"entities": {}, "correction": None}),
    )

    result = await step.execute(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch == {"recipient_phone": "08162511023"}


@pytest.mark.asyncio
async def test_airtime_extraction_reuses_resolved_phone_referent_without_extractor() -> None:
    step = ExtractionStep("buy airtime for that number")
    payload = AirtimePayload(amount=5000)
    context = AirtimeContext(
        phone_number="2348000000000",
        language="en",
        resolved_referents={
            "phone": {
                "status": "resolved",
                "item": {
                    "label": "Mum",
                    "data": {"phone": "08162511023", "network": "mtn"},
                },
            }
        },
    )
    gates = AirtimeGates()
    worker_context = SimpleNamespace(required_fields=["recipient_phone"], extractor=None)

    result = await step.execute(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch == {"recipient_phone": "08162511023", "network": "MTN"}


@pytest.mark.asyncio
async def test_airtime_extraction_reuses_repeat_amount_and_source_referents_without_extractor() -> None:
    step = ExtractionStep("buy airtime for that number again from same account")
    payload = AirtimePayload()
    context = AirtimeContext(
        phone_number="2348000000000",
        language="en",
        resolved_referents={
            "phone": {
                "status": "resolved",
                "item": {"label": "Mum", "data": {"phone": "08162511023", "network": "mtn"}},
            },
            "amount": {
                "status": "resolved",
                "item": {"label": "2000", "data": {"amount": 2000}},
            },
            "source_account": {
                "status": "resolved",
                "item": {
                    "label": "Kuda",
                    "data": {
                        "source_account_id": "acc-kuda",
                        "source_bank_name": "Kuda",
                        "source_account_name": "Kuda Main",
                        "source_account_number": "0000000001",
                    },
                },
            },
        },
    )
    gates = AirtimeGates()
    worker_context = SimpleNamespace(required_fields=["recipient_phone", "amount"], extractor=None)

    result = await step.execute(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch == {
        "recipient_phone": "08162511023",
        "network": "MTN",
        "amount": 2000,
        "source_account_id": "acc-kuda",
        "source_bank_name": "Kuda",
        "source_account_name": "Kuda Main",
        "source_account_number": "0000000001",
        "source_account_index": None,
    }


@pytest.mark.asyncio
async def test_airtime_extraction_ambiguous_phone_referent_prompts_with_candidates() -> None:
    step = ExtractionStep("buy airtime for that number")
    payload = AirtimePayload(amount=1000)
    context = AirtimeContext(
        phone_number="2348000000000",
        language="en",
        resolved_referents={
            "phone": {
                "status": "ambiguous",
                "candidates": [
                    {"label": "Mum", "data": {"phone": "08162511023", "network": "mtn"}},
                    {"label": "Dad", "data": {"phone": "08031234567", "network": "airtel"}},
                ],
            }
        },
    )
    gates = AirtimeGates()
    worker_context = SimpleNamespace(required_fields=["recipient_phone"], extractor=None)

    result = await step.execute(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.required_fields == ["referent_phone_id"]
    assert "Which number" in str(result.prompt)
    assert "Reply with the number" in str(result.prompt)
    assert result.patch["referent_phone_candidates"][0]["recipient_phone"] == "08162511023"


@pytest.mark.asyncio
async def test_airtime_extraction_accepts_numeric_referent_phone_selection() -> None:
    step = ExtractionStep("2")
    payload = AirtimePayload(
        amount=1000,
        referent_phone_candidates=[
            {
                "index": 1,
                "option_id": "phone:08162511023",
                "label": "Mum • 08162511023 • MTN",
                "recipient_phone": "08162511023",
                "network": "MTN",
            },
            {
                "index": 2,
                "option_id": "phone:08031234567",
                "label": "Dad • 08031234567 • AIRTEL",
                "recipient_phone": "08031234567",
                "network": "AIRTEL",
            },
        ],
    )
    context = AirtimeContext(phone_number="2348000000000", language="en")
    gates = AirtimeGates()
    worker_context = SimpleNamespace(required_fields=["referent_phone_id"], extractor=None)

    result = await step.execute(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["recipient_phone"] == "08031234567"
    assert result.patch["network"] == "AIRTEL"
    assert result.patch["referent_phone_candidates"] == []


@pytest.mark.asyncio
async def test_airtime_extraction_passes_compact_context_to_extractor() -> None:
    step = ExtractionStep("08162511023")
    payload = AirtimePayload(amount=5000, recipient_name="Mum")
    context = AirtimeContext(
        phone_number="2348000000000",
        language="en",
        beneficiaries=[{"alias": name} for name in ("Mum", "Tolu", "Doyin", "Gaines", "Dad")],
        accounts=[{"bank_name": "First Bank", "account_number": "0000000001"}],
    )
    gates = AirtimeGates()
    extractor = _ExtractorStub({"entities": {"recipient_phone": "08162511023"}, "correction": None})
    worker_context = SimpleNamespace(
        required_fields=["recipient_phone"],
        previous_response="Which phone number?",
        extractor=extractor,
    )

    result = await step.execute(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert extractor.last_state is not None
    assert extractor.last_state["required_fields"] == ["recipient_phone"]
    assert extractor.last_state["previousResponse"] == "Which phone number?"
    assert extractor.last_state["beneficiaries"][0]["alias"] == "Mum"


@pytest.mark.asyncio
async def test_airtime_extraction_self_fallback_sets_is_self_when_extractor_misses() -> None:
    step = ExtractionStep("buy me 5k airtime")
    payload = AirtimePayload(amount=5000)
    context = AirtimeContext(phone_number="2348000000000", language="en")
    gates = AirtimeGates()
    worker_context = SimpleNamespace(
        required_fields=[],
        extractor=_ExtractorStub({"entities": {}, "correction": None}),
    )

    result = await step.execute(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch == {"is_self": True}


@pytest.mark.asyncio
async def test_airtime_extraction_self_fallback_skips_when_phone_provided_by_extractor() -> None:
    step = ExtractionStep("buy me 5k airtime")
    payload = AirtimePayload(amount=5000)
    context = AirtimeContext(phone_number="2348000000000", language="en")
    gates = AirtimeGates()
    worker_context = SimpleNamespace(
        required_fields=[],
        extractor=_ExtractorStub({"entities": {"recipient_phone": "08162511023"}, "correction": None}),
    )

    result = await step.execute(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch == {"recipient_phone": "08162511023"}


@pytest.mark.asyncio
async def test_airtime_validation_requests_network_when_phone_present() -> None:
    step = ValidationStep()
    payload = AirtimePayload(amount=5000, recipient_phone="08162511023", network=None)
    context = AirtimeContext(phone_number="2348000000000", language="en")
    gates = AirtimeGates()
    worker_context = SimpleNamespace()

    result = await step.execute(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.required_fields == ["network"]
    assert "Which network is 08162511023?" in (result.prompt or "")


@pytest.mark.asyncio
async def test_airtime_validation_missing_fields_uses_locale_field_labels() -> None:
    step = ValidationStep()
    payload = AirtimePayload()
    context = AirtimeContext(phone_number="2348000000000", language="yo")
    gates = AirtimeGates()
    worker_context = SimpleNamespace()

    result = await step.execute(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.required_fields == ["recipient_phone", "amount"]
    assert result.prompt == "Jowo fi nomba foonu ati iye owo ranse."


@pytest.mark.asyncio
async def test_airtime_validation_rejects_invalid_phone_shape() -> None:
    step = ValidationStep()
    payload = AirtimePayload(amount=5000, recipient_phone="816251", network="MTN")
    context = AirtimeContext(phone_number="2348000000000", language="en")
    gates = AirtimeGates()
    worker_context = SimpleNamespace()

    result = await step.execute(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.required_fields == ["recipient_phone"]
    assert result.prompt == "Please enter a valid Nigerian phone number."


@pytest.mark.asyncio
async def test_airtime_validation_invalid_phone_uses_locale_copy() -> None:
    step = ValidationStep()
    payload = AirtimePayload(amount=5000, recipient_phone="816251", network="MTN")
    context = AirtimeContext(phone_number="2348000000000", language="pcm")
    gates = AirtimeGates()
    worker_context = SimpleNamespace()

    result = await step.execute(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.required_fields == ["recipient_phone"]
    assert result.prompt == "Abeg enter correct Naija phone number."


@pytest.mark.asyncio
async def test_airtime_validation_rejects_invalid_network_name() -> None:
    step = ValidationStep()
    payload = AirtimePayload(amount=5000, recipient_phone="08162511023", network="vodafone")
    context = AirtimeContext(phone_number="2348000000000", language="en")
    gates = AirtimeGates()
    worker_context = SimpleNamespace()

    result = await step.execute(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.required_fields == ["network"]
    assert "Invalid network." in (result.prompt or "")


@pytest.mark.asyncio
async def test_airtime_skip_extraction_overrides_for_phone_signal() -> None:
    step = ExtractionStep("816 251 1023")
    payload = AirtimePayload(amount=5000, skip_extraction=True)
    context = AirtimeContext(phone_number="2348000000000", language="en")
    gates = AirtimeGates()
    extractor = _ExtractorStub({"entities": {"recipient_phone": "08162511023"}, "correction": None})
    worker_context = SimpleNamespace(required_fields=[], extractor=extractor)

    result = await step.execute(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert extractor.calls == 1
    assert result.patch == {"recipient_phone": "08162511023", "skip_extraction": False}


@pytest.mark.asyncio
async def test_airtime_skip_extraction_overrides_for_network_signal() -> None:
    step = ExtractionStep("mtn")
    payload = AirtimePayload(amount=5000, recipient_phone="08162511023", skip_extraction=True)
    context = AirtimeContext(phone_number="2348000000000", language="en")
    gates = AirtimeGates()
    extractor = _ExtractorStub({"entities": {"network": "mtn"}, "correction": None})
    worker_context = SimpleNamespace(required_fields=[], extractor=extractor)

    result = await step.execute(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert extractor.calls == 1
    assert result.patch == {"network": "MTN", "skip_extraction": False}


@pytest.mark.asyncio
async def test_airtime_skip_extraction_overrides_for_self_signal() -> None:
    step = ExtractionStep("buy me 5k airtime")
    payload = AirtimePayload(amount=5000, skip_extraction=True)
    context = AirtimeContext(phone_number="2348000000000", language="en")
    gates = AirtimeGates()
    extractor = _ExtractorStub({"entities": {}, "correction": None})
    worker_context = SimpleNamespace(required_fields=[], extractor=extractor)

    result = await step.execute(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert extractor.calls == 1
    assert result.patch == {"is_self": True, "skip_extraction": False}


@pytest.mark.asyncio
async def test_airtime_skip_extraction_skips_without_strong_signal() -> None:
    step = ExtractionStep("okay")
    payload = AirtimePayload(amount=5000, skip_extraction=True)
    context = AirtimeContext(phone_number="2348000000000", language="en")
    gates = AirtimeGates()
    extractor = _ExtractorStub({"entities": {"recipient_phone": "08162511023"}, "correction": None})
    worker_context = SimpleNamespace(required_fields=[], extractor=extractor)

    result = await step.execute(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert extractor.calls == 0
    assert result.patch == {"skip_extraction": False}
