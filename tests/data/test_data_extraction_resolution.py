from types import SimpleNamespace

import pytest

from apps.chat.src.agent.graphs.data.models.types import DataContext, DataGates, DataPayload
from apps.chat.src.agent.graphs.data.models_extraction import DataExtractionResult, DataPurchaseEntities
from apps.chat.src.agent.graphs.data.nodes.confirmation import ConfirmationStep
from apps.chat.src.agent.graphs.data.nodes.extraction import ExtractionStep
from apps.chat.src.agent.graphs.data.nodes.resolution import ResolutionStep
from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome


class _ExtractorStub:
    def __init__(self, result: DataExtractionResult) -> None:
        self._result = result
        self.calls = 0
        self.last_context: dict | None = None

    async def extract(self, _message: str, smart_context: dict | None = None) -> DataExtractionResult:
        self.calls += 1
        self.last_context = smart_context
        return self._result


@pytest.mark.asyncio
async def test_data_skip_extraction_overrides_for_phone_signal() -> None:
    step = ExtractionStep("816 251 1023")
    payload = DataPayload(skip_extraction=True)
    context = DataContext(phone_number="2348000000000", language="en")
    gates = DataGates()
    extractor = _ExtractorStub(
        DataExtractionResult(entities=DataPurchaseEntities(recipient_phone="08162511023")),
    )
    worker_context = SimpleNamespace(required_fields=[], extractor=extractor)

    result = await step.run(payload, context, gates, worker_context)

    assert result is None
    assert extractor.calls == 1
    assert payload.skip_extraction is False
    assert payload.target_phone == "08162511023"
    assert payload.stage == "extracted"


@pytest.mark.asyncio
async def test_data_extraction_reuses_resolved_phone_referent_without_extractor() -> None:
    step = ExtractionStep("buy data for that number")
    payload = DataPayload()
    context = DataContext(
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
    gates = DataGates()
    worker_context = SimpleNamespace(required_fields=["target_phone"], extractor=None)

    result = await step.run(payload, context, gates, worker_context)

    assert result is None
    assert payload.target_phone == "08162511023"
    assert payload.network == "MTN"


@pytest.mark.asyncio
async def test_data_extraction_passes_compact_context_to_extractor() -> None:
    step = ExtractionStep("08162511023")
    payload = DataPayload(network="MTN")
    context = DataContext(
        phone_number="2348000000000",
        language="en",
        beneficiaries=[{"alias": "Mum"}],
        accounts=[{"bank_name": "First Bank", "account_number": "0000000001"}],
    )
    gates = DataGates()
    extractor = _ExtractorStub(
        DataExtractionResult(entities=DataPurchaseEntities(recipient_phone="08162511023")),
    )
    worker_context = SimpleNamespace(
        required_fields=["target_phone"],
        previous_response="Which line should I buy data for?",
        extractor=extractor,
    )

    result = await step.run(payload, context, gates, worker_context)

    assert result is None
    assert extractor.last_context is not None
    assert extractor.last_context["required_fields"] == ["target_phone"]
    assert extractor.last_context["previousResponse"] == "Which line should I buy data for?"
    assert extractor.last_context["network"] == "MTN"


@pytest.mark.asyncio
async def test_data_skip_extraction_overrides_for_network_signal() -> None:
    step = ExtractionStep("mtn")
    payload = DataPayload(skip_extraction=True, target_phone="08162511023")
    context = DataContext(phone_number="2348000000000", language="en")
    gates = DataGates()
    extractor = _ExtractorStub(
        DataExtractionResult(entities=DataPurchaseEntities(network="MTN")),
    )
    worker_context = SimpleNamespace(required_fields=[], extractor=extractor)

    result = await step.run(payload, context, gates, worker_context)

    assert result is None
    assert extractor.calls == 1
    assert payload.skip_extraction is False
    assert payload.network == "MTN"
    assert payload.stage == "extracted"


@pytest.mark.asyncio
async def test_data_skip_extraction_skips_without_strong_signal() -> None:
    step = ExtractionStep("okay")
    payload = DataPayload(skip_extraction=True)
    context = DataContext(phone_number="2348000000000", language="en")
    gates = DataGates()
    extractor = _ExtractorStub(DataExtractionResult())
    worker_context = SimpleNamespace(required_fields=[], extractor=extractor)

    result = await step.run(payload, context, gates, worker_context)

    assert result is None
    assert extractor.calls == 0
    assert payload.skip_extraction is False
    assert payload.stage == "init"


@pytest.mark.asyncio
async def test_data_resolution_normalizes_phone_and_infers_network() -> None:
    step = ResolutionStep()
    payload = DataPayload(target_phone="816 251 1023")
    context = DataContext(phone_number="2348000000000", language="en")
    gates = DataGates()

    result = await step.run(payload, context, gates, SimpleNamespace())

    assert result is None
    assert payload.target_phone == "08162511023"
    assert payload.network == "MTN"


@pytest.mark.asyncio
async def test_data_resolution_asks_when_network_unresolved_from_phone() -> None:
    step = ResolutionStep()
    payload = DataPayload(target_phone="07001234567")
    context = DataContext(phone_number="2348000000000", language="en")
    gates = DataGates()

    result = await step.run(payload, context, gates, SimpleNamespace())

    assert result is not None
    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.required_fields == ["network"]
    assert "07001234567" in (result.prompt or "")


@pytest.mark.asyncio
async def test_data_resolution_normalizes_network_alias() -> None:
    step = ResolutionStep()
    payload = DataPayload(target_phone="08162511023", network="airtel")
    context = DataContext(phone_number="2348000000000", language="en")
    gates = DataGates()

    result = await step.run(payload, context, gates, SimpleNamespace())

    assert result is None
    assert payload.network == "AIRTEL"


@pytest.mark.asyncio
async def test_data_confirmation_uses_shared_summary_when_amount_is_known() -> None:
    step = ConfirmationStep()
    payload = DataPayload(
        amount=1500,
        plan_name="MTN 2GB",
        network="MTN",
        target_phone="08162511023",
        source_bank_name="First Bank",
        source_account_number="1234567890",
    )
    context = DataContext(phone_number="2348000000000", language="en")
    gates = DataGates()

    result = await step.run(payload, context, gates, SimpleNamespace())

    assert result is not None
    assert result.outcome == TransactionOutcome.NEEDS_CONFIRMATION
    assert result.confirmation_summary == "*MTN 2GB → 08162511023*\nNetwork: MTN • Amount: ₦1,500\n\nFrom: First Bank (···7890)"
    assert result.confirmation_snapshot == payload.model_dump(mode="json")


@pytest.mark.asyncio
async def test_data_confirmation_keeps_short_prompt_when_amount_is_unknown() -> None:
    step = ConfirmationStep()
    payload = DataPayload(network="MTN", target_phone="08162511023")
    context = DataContext(phone_number="2348000000000", language="en")
    gates = DataGates()

    result = await step.run(payload, context, gates, SimpleNamespace())

    assert result is not None
    assert result.outcome == TransactionOutcome.NEEDS_CONFIRMATION
    assert result.confirmation_summary == "Buy MTN data for 08162511023?"
