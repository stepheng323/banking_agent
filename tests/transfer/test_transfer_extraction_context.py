"""Transfer extraction context propagation tests."""

from types import SimpleNamespace

from apps.core.src.agent.graphs.transfer.models.extraction import TransferExtractionResult
from apps.core.src.agent.graphs.transfer.models.types import TransferContext, TransferGates, TransferPayload
from apps.core.src.agent.graphs.transfer.nodes.extraction import ExtractionStep
from apps.core.src.agent.graphs.transfer.services.extractor import TransferEntityExtractor
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome


class _CaptureExtractor:
    def __init__(self) -> None:
        self.last_user_message: str | None = None
        self.last_smart_context: dict | None = None

    async def extract(self, text: str, smart_context: dict | None = None) -> TransferExtractionResult:
        self.last_user_message = text
        self.last_smart_context = smart_context
        return TransferExtractionResult()


async def test_extraction_step_passes_required_fields_previous_response_and_known_recipient() -> None:
    capture_extractor = _CaptureExtractor()
    step = ExtractionStep(user_message="Access bank")
    payload = TransferPayload(recipient_name="Tolu")
    context = TransferContext(
        phone_number="2348000000999",
        language="en",
        beneficiaries=[{"alias": "Mum"}],
        accounts=[{"bank_name": "First Bank"}],
    )
    worker_context = SimpleNamespace(
        extractor=capture_extractor,
        required_fields=["recipient_account", "recipient_bank_name"],
        previous_response="What's Tolu's account number and bank?",
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert capture_extractor.last_user_message == "Access bank"
    assert capture_extractor.last_smart_context is not None
    assert capture_extractor.last_smart_context["required_fields"] == ["recipient_account", "recipient_bank_name"]
    assert capture_extractor.last_smart_context["previousResponse"] == worker_context.previous_response
    assert capture_extractor.last_smart_context["known_recipient"] == {
        "recipient_name": "Tolu",
        "recipient_resolved_name": None,
        "recipient_account": None,
        "recipient_bank_name": None,
    }


def test_extractor_context_string_includes_required_fields_and_known_recipient_hints() -> None:
    extractor = TransferEntityExtractor.__new__(TransferEntityExtractor)
    context_str = extractor._build_context_string(
        {
            "required_fields": ["recipient_account", "recipient_bank_name"],
            "previousResponse": "Need recipient details",
            "known_recipient": {
                "recipient_name": "Tolu",
                "recipient_bank_name": "Access Bank",
            },
            "language": "en",
        }
    )

    assert "LastMsg: Need recipient details" in context_str
    assert "RequiredFields: recipient_account, recipient_bank_name" in context_str
    assert "KnownRecipientName: Tolu" in context_str
    assert "KnownRecipientBank: Access Bank" in context_str
