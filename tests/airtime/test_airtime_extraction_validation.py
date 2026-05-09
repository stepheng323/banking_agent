from types import SimpleNamespace

import pytest

from apps.chat.src.agent.graphs.airtime.models.types import AirtimeContext, AirtimeGates, AirtimePayload
from apps.chat.src.agent.graphs.airtime.nodes.extraction import ExtractionStep
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
async def test_airtime_validation_rejects_invalid_phone_shape() -> None:
    step = ValidationStep()
    payload = AirtimePayload(amount=5000, recipient_phone="816251", network="MTN")
    context = AirtimeContext(phone_number="2348000000000", language="en")
    gates = AirtimeGates()
    worker_context = SimpleNamespace()

    result = await step.execute(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.required_fields == ["recipient_phone"]


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
