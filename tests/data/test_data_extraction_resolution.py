from types import SimpleNamespace
from uuid import uuid4

import pytest

from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome
from apps.chat.src.agent.workers.data.models.extraction import DataExtractionResult, DataPurchaseEntities
from apps.chat.src.agent.workers.data.models.types import DataContext, DataGates, DataPayload
from apps.chat.src.agent.workers.data.nodes.confirmation import ConfirmationStep
from apps.chat.src.agent.workers.data.nodes.extraction import ExtractionStep
from apps.chat.src.agent.workers.data.nodes.resolution import ResolutionStep


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

    assert result.outcome == TransactionOutcome.OK
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

    assert result.outcome == TransactionOutcome.OK
    assert payload.target_phone == "08162511023"
    assert payload.network == "MTN"


@pytest.mark.asyncio
async def test_data_extraction_reuses_repeat_referents_without_extractor() -> None:
    step = ExtractionStep("buy data for that number again from same account")
    payload = DataPayload()
    context = DataContext(
        phone_number="2348000000000",
        language="en",
        resolved_referents={
            "phone": {
                "status": "resolved",
                "item": {"label": "Mum", "data": {"phone": "08162511023", "network": "mtn"}},
            },
            "amount": {
                "status": "resolved",
                "item": {"label": "1500", "data": {"amount": 1500}},
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
    gates = DataGates()
    worker_context = SimpleNamespace(required_fields=["target_phone"], extractor=None)

    result = await step.run(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert payload.target_phone == "08162511023"
    assert payload.network == "MTN"
    assert payload.amount == 1500
    assert payload.source_account_id == "acc-kuda"
    assert payload.source_bank_name == "Kuda"
    assert payload.source_account_name == "Kuda Main"
    assert payload.source_account_number == "0000000001"


@pytest.mark.asyncio
async def test_data_source_account_slot_accepts_bank_reference_without_extractor() -> None:
    step = ExtractionStep("my GTB")
    payload = DataPayload(
        amount=3500,
        target_phone="08162511023",
        network="MTN",
        plan_code="MD501",
        plan_name="MTN 5 GB data bundle",
    )
    context = DataContext(
        phone_number="2348000000000",
        language="en",
        accounts=[
            {
                "id": "access-1",
                "bank_name": "Access Bank",
                "account_name": "Access Main",
                "account_number": "0000000003",
            },
            {
                "id": "gtb-1",
                "bank_name": "GTBank",
                "account_name": "GT Main",
                "account_number": "0000000002",
            },
        ],
    )
    gates = DataGates()
    worker_context = SimpleNamespace(required_fields=["source_account_id"], extractor=None)

    result = await step.run(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert payload.source_account_id == "gtb-1"
    assert payload.source_bank_name == "GTBank"
    assert payload.source_account_name == "GT Main"
    assert payload.source_account_number == "0000000002"
    assert payload.source_account_index is None
    assert payload.stage == "extracted"


@pytest.mark.asyncio
async def test_data_source_account_numeric_slot_clears_stale_source_before_reselection() -> None:
    step = ExtractionStep("2")
    payload = DataPayload(
        amount=3500,
        target_phone="08162511023",
        network="MTN",
        plan_code="MD501",
        plan_name="MTN 5 GB data bundle",
        source_account_id="old-account",
        confirmation={"confirmed": True},
    )
    context = DataContext(phone_number="2348000000000", language="en")
    gates = DataGates()
    worker_context = SimpleNamespace(required_fields=["source_account_id"], extractor=None)

    result = await step.run(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert payload.source_account_index == 2
    assert payload.source_account_id is None
    assert payload.confirmation == {"confirmed": False}
    assert payload.stage == "extracted"


@pytest.mark.asyncio
async def test_data_extraction_self_line_reply_sets_context_phone_without_extractor() -> None:
    step = ExtractionStep("my line")
    payload = DataPayload(network="MTN")
    context = DataContext(phone_number="2348162511023", language="en")
    gates = DataGates()
    worker_context = SimpleNamespace(required_fields=["target_phone"], extractor=None)

    result = await step.run(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert payload.target_phone == "08162511023"
    assert payload.is_self is True
    assert payload.skip_extraction is False
    assert payload.stage == "extracted"


@pytest.mark.asyncio
async def test_data_extraction_phone_reply_sets_target_phone_without_extractor() -> None:
    step = ExtractionStep("0816 251 1023")
    payload = DataPayload(network="MTN")
    context = DataContext(phone_number="2348000000000", language="en")
    gates = DataGates()
    worker_context = SimpleNamespace(required_fields=["target_phone"], extractor=None)

    result = await step.run(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert payload.target_phone == "08162511023"
    assert payload.is_self is False
    assert payload.stage == "extracted"


@pytest.mark.asyncio
async def test_data_extraction_network_reply_sets_network_without_extractor() -> None:
    step = ExtractionStep("MTN")
    payload = DataPayload(target_phone="08162511023")
    context = DataContext(phone_number="2348000000000", language="en")
    gates = DataGates()
    worker_context = SimpleNamespace(required_fields=["network"], extractor=None)

    result = await step.run(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert payload.network == "MTN"
    assert payload.skip_extraction is False
    assert payload.stage == "extracted"


@pytest.mark.asyncio
async def test_data_extraction_network_reply_sets_network_in_multi_slot_prompt() -> None:
    step = ExtractionStep("MTN")
    payload = DataPayload(target_phone="08162511023")
    context = DataContext(phone_number="2348000000000", language="en")
    gates = DataGates()
    worker_context = SimpleNamespace(required_fields=["network", "data_plan_preference"], extractor=None)

    result = await step.run(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert payload.network == "MTN"
    assert payload.skip_extraction is False
    assert payload.stage == "extracted"


@pytest.mark.asyncio
async def test_data_extraction_ambiguous_phone_referent_prompts_with_candidates() -> None:
    step = ExtractionStep("buy data for that number")
    payload = DataPayload()
    context = DataContext(
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
    gates = DataGates()
    worker_context = SimpleNamespace(required_fields=["target_phone"], extractor=None)

    result = await step.run(payload, context, gates, worker_context)

    assert result is not None
    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.required_fields == ["referent_phone_id"]
    assert "Which number" in str(result.prompt)
    assert "Reply with the number" in str(result.prompt)
    assert result.patch["referent_phone_candidates"][1]["target_phone"] == "08031234567"


@pytest.mark.asyncio
async def test_data_extraction_accepts_numeric_referent_phone_selection() -> None:
    step = ExtractionStep("1")
    payload = DataPayload(
        referent_phone_candidates=[
            {
                "index": 1,
                "option_id": "phone:08162511023",
                "label": "Mum • 08162511023 • MTN",
                "target_phone": "08162511023",
                "network": "MTN",
            },
            {
                "index": 2,
                "option_id": "phone:08031234567",
                "label": "Dad • 08031234567 • AIRTEL",
                "target_phone": "08031234567",
                "network": "AIRTEL",
            },
        ],
    )
    context = DataContext(phone_number="2348000000000", language="en")
    gates = DataGates()
    worker_context = SimpleNamespace(required_fields=["referent_phone_id"], extractor=None)

    result = await step.run(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert payload.target_phone == "08162511023"
    assert payload.network == "MTN"
    assert payload.referent_phone_candidates == []


@pytest.mark.asyncio
async def test_data_extraction_passes_compact_context_to_extractor() -> None:
    step = ExtractionStep("buy it for mum")
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

    assert result.outcome == TransactionOutcome.OK
    assert extractor.last_context is not None
    assert extractor.last_context["required_fields"] == ["target_phone"]
    assert extractor.last_context["previousResponse"] == "Which line should I buy data for?"
    assert extractor.last_context["network"] == "MTN"


@pytest.mark.asyncio
async def test_data_extraction_records_recipient_name_for_resolution() -> None:
    step = ExtractionStep("buy data for mum")
    payload = DataPayload(network="MTN")
    context = DataContext(phone_number="2348000000000", language="en")
    gates = DataGates()
    extractor = _ExtractorStub(
        DataExtractionResult(entities=DataPurchaseEntities(recipient_name="Mum")),
    )
    worker_context = SimpleNamespace(required_fields=[], extractor=extractor)

    result = await step.run(payload, context, gates, worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert payload.recipient_name == "Mum"


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

    assert result.outcome == TransactionOutcome.OK
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

    assert result.outcome == TransactionOutcome.OK
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

    assert result.outcome == TransactionOutcome.OK
    assert payload.target_phone == "08162511023"
    assert payload.network == "MTN"


@pytest.mark.asyncio
async def test_data_resolution_missing_target_phone_defaults_to_user_number() -> None:
    step = ResolutionStep()
    payload = DataPayload()
    context = DataContext(phone_number="2348000000000", language="pcm")
    gates = DataGates()

    result = await step.run(payload, context, gates, SimpleNamespace())

    assert result is not None
    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.required_fields == ["network"]
    assert result.prompt == "Which network dey on 08000000000?"
    assert payload.target_phone == "08000000000"
    assert payload.is_self is True


@pytest.mark.asyncio
async def test_data_resolution_saved_mobile_beneficiary_sets_phone_network_and_id() -> None:
    beneficiary_id = uuid4()
    step = ResolutionStep()
    payload = DataPayload(recipient_name="Mum")
    context = DataContext(
        phone_number="+2348162511023",
        language="en",
        beneficiaries=[
            {
                "id": beneficiary_id,
                "beneficiary_type": "data",
                "alias": "Mum",
                "account_name": "Mum",
                "account_number": "08081234567",
                "bank_name": "Airtel",
            }
        ],
    )
    gates = DataGates()

    result = await step.run(payload, context, gates, SimpleNamespace())

    assert result.outcome == TransactionOutcome.OK
    assert payload.target_phone == "08081234567"
    assert payload.recipient_name == "Mum"
    assert payload.beneficiary_id == str(beneficiary_id)
    assert payload.is_self is False
    assert payload.network == "AIRTEL"


@pytest.mark.asyncio
async def test_data_resolution_ignores_transfer_beneficiary_with_same_alias() -> None:
    step = ResolutionStep()
    payload = DataPayload(recipient_name="Mum")
    context = DataContext(
        phone_number="+2348162511023",
        language="en",
        beneficiaries=[
            {
                "id": uuid4(),
                "beneficiary_type": "transfer",
                "alias": "Mum",
                "account_name": "Mum",
                "account_number": "2010000001",
                "bank_name": "GTBank",
            }
        ],
    )
    gates = DataGates()

    result = await step.run(payload, context, gates, SimpleNamespace())

    assert result is not None
    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.required_fields == ["target_phone"]
    assert payload.target_phone is None
    assert payload.is_self is False


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
async def test_data_resolution_network_prompt_uses_locale_copy() -> None:
    step = ResolutionStep()
    payload = DataPayload(target_phone="07001234567")
    context = DataContext(phone_number="2348000000000", language="yo")
    gates = DataGates()

    result = await step.run(payload, context, gates, SimpleNamespace())

    assert result is not None
    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.required_fields == ["network"]
    assert result.prompt == "Network wo ni 07001234567 wa lori?"


@pytest.mark.asyncio
async def test_data_resolution_normalizes_network_alias() -> None:
    step = ResolutionStep()
    payload = DataPayload(target_phone="08162511023", network="airtel")
    context = DataContext(phone_number="2348000000000", language="en")
    gates = DataGates()

    result = await step.run(payload, context, gates, SimpleNamespace())

    assert result.outcome == TransactionOutcome.OK
    assert payload.network == "AIRTEL"


@pytest.mark.asyncio
async def test_data_confirmation_uses_shared_summary_when_amount_is_known() -> None:
    step = ConfirmationStep()
    payload = DataPayload(
        amount=1500,
        plan_code="mtn-2gb",
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
    assert (
        result.confirmation_summary
        == "*MTN 2GB for 08162511023*\nNetwork: MTN • Amount: ₦1,500\n\nFrom: First Bank (···7890)"
    )
    assert result.confirmation_snapshot == {
        "amount": 1500,
        "network": "MTN",
        "target_phone": "08162511023",
        "plan_code": "mtn-2gb",
        "plan_name": "MTN 2GB",
        "biller_code": None,
        "plan_size_gb": None,
        "plan_validity_days": None,
        "source_bank_name": "First Bank",
        "source_account_number": "1234567890",
        "is_self": False,
    }


@pytest.mark.asyncio
async def test_data_confirmation_update_message_acknowledges_reselected_plan() -> None:
    step = ConfirmationStep()
    payload = DataPayload(
        amount=2000,
        plan_code="mtn-35gb",
        plan_name="MTN 3.5 GB",
        network="MTN",
        target_phone="08162511023",
        source_bank_name="First Bank",
        source_account_number="1234567890",
        previous_confirmation_snapshot={
            "amount": 3500,
            "network": "MTN",
            "target_phone": "08162511023",
            "plan_code": "mtn-5gb",
            "plan_name": "MTN 5 GB data bundle",
        },
    )
    context = DataContext(phone_number="2348000000000", language="en")
    gates = DataGates()

    result = await step.run(payload, context, gates, SimpleNamespace())

    assert result is not None
    assert result.outcome == TransactionOutcome.NEEDS_CONFIRMATION
    assert result.update_message == "Got it, using MTN 3.5 GB at ₦2,000."


@pytest.mark.asyncio
async def test_data_confirmation_self_summary_uses_plan_first_copy_with_phone() -> None:
    step = ConfirmationStep()
    payload = DataPayload(
        amount=3500,
        plan_code="mtn-5gb",
        plan_name="MTN 5 GB data bundle",
        network="MTN",
        target_phone="08162511023",
        is_self=True,
        source_bank_name="GTBank",
        source_account_number="2010000002",
    )
    context = DataContext(phone_number="2348162511023", language="en")
    gates = DataGates()

    result = await step.run(payload, context, gates, SimpleNamespace())

    assert result is not None
    assert result.outcome == TransactionOutcome.NEEDS_CONFIRMATION
    assert (
        result.confirmation_summary == "*MTN 5 GB data bundle for your number (08162511023)*\n"
        "Network: MTN • Amount: ₦3,500\n\n"
        "From: GTBank (···0002)"
    )


@pytest.mark.asyncio
async def test_data_confirmation_requires_catalog_plan() -> None:
    step = ConfirmationStep()
    payload = DataPayload(network="MTN", target_phone="08162511023")
    context = DataContext(phone_number="2348000000000", language="en")
    gates = DataGates()

    result = await step.run(payload, context, gates, SimpleNamespace())

    assert result is not None
    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.required_fields == ["data_plan_id"]
    assert result.prompt == "Please choose a valid data plan before we continue."


@pytest.mark.asyncio
async def test_data_confirmation_missing_plan_uses_locale_copy() -> None:
    step = ConfirmationStep()
    payload = DataPayload(network="MTN", target_phone="08162511023")
    context = DataContext(phone_number="2348000000000", language="yo")
    gates = DataGates()

    result = await step.run(payload, context, gates, SimpleNamespace())

    assert result is not None
    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.prompt == "Jowo yan eto data to pe ki a to tesiwaju."
