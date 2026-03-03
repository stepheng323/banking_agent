"""Transfer extraction context propagation tests."""

from types import SimpleNamespace
from uuid import uuid4

from apps.core.src.agent.graphs.transfer.models.entities import TransferEntities
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


async def test_extraction_step_numeric_reply_selects_beneficiary_when_awaiting_beneficiary_id() -> None:
    first_id = str(uuid4())
    second_id = str(uuid4())
    step = ExtractionStep(user_message="2")
    payload = TransferPayload(recipient_name="john")
    context = TransferContext(
        phone_number="2348000000999",
        language="en",
        beneficiaries=[
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
        accounts=[],
    )
    worker_context = SimpleNamespace(
        extractor=None,
        required_fields=["beneficiary_id"],
        previous_response="I found multiple matches.",
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["beneficiary_id"] == second_id
    assert "source_account_index" not in result.patch


async def test_extraction_step_accepts_option_id_for_beneficiary_selection() -> None:
    first_id = str(uuid4())
    second_id = str(uuid4())
    step = ExtractionStep(user_message=f"bene:{second_id}")
    payload = TransferPayload(
        recipient_name="john",
        beneficiary_candidates=[
            {"index": 1, "beneficiary_id": first_id, "option_id": f"bene:{first_id}", "label": "John Doe"},
            {"index": 2, "beneficiary_id": second_id, "option_id": f"bene:{second_id}", "label": "John Smith"},
        ],
    )
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=None,
        required_fields=["beneficiary_id"],
        previous_response="I found multiple matches.",
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["beneficiary_id"] == second_id
    assert result.patch["beneficiary_candidates"] == []


async def test_extraction_step_invalid_numeric_beneficiary_selection_reprompts() -> None:
    first_id = str(uuid4())
    second_id = str(uuid4())
    step = ExtractionStep(user_message="9")
    payload = TransferPayload(
        recipient_name="john",
        beneficiary_candidates=[
            {"index": 1, "beneficiary_id": first_id, "option_id": f"bene:{first_id}", "label": "John Doe"},
            {"index": 2, "beneficiary_id": second_id, "option_id": f"bene:{second_id}", "label": "John Smith"},
        ],
    )
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=None,
        required_fields=["beneficiary_id"],
        previous_response="I found multiple matches.",
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.required_fields == ["beneficiary_id"]
    assert "Reply with the number or rephrase." in (result.prompt or "")
    assert result.details["ambiguity"] == "MULTIPLE_BENEFICIARIES"


async def test_extraction_step_suggested_amount_option_1_uses_previous_amount() -> None:
    step = ExtractionStep(user_message="1")
    payload = TransferPayload(recipient_name="Tolu", suggested_amount=5000)
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=None,
        required_fields=["amount"],
        previous_response="How much should I send to Tolu?",
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["amount"] == 5000
    assert result.patch["suggested_amount"] is None


async def test_extraction_step_suggested_amount_option_2_clears_suggestion() -> None:
    step = ExtractionStep(user_message="2")
    payload = TransferPayload(recipient_name="Tolu", suggested_amount=5000)
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=None,
        required_fields=["amount"],
        previous_response="How much should I send to Tolu?",
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["suggested_amount"] is None
    assert "source_account_index" not in result.patch


async def test_extraction_step_ignores_numeric_source_selection_when_not_awaiting_source_account() -> None:
    step = ExtractionStep(user_message="2")
    payload = TransferPayload(recipient_name="Tolu")
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=None,
        required_fields=["amount"],
        previous_response="How much should I send?",
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert "source_account_index" not in result.patch


async def test_extraction_step_supports_multi_digit_source_account_selection() -> None:
    step = ExtractionStep(user_message="10")
    payload = TransferPayload(recipient_name="Tolu")
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=None,
        required_fields=["source_account_id"],
        previous_response="Which account should I use?",
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["source_account_index"] == 10


async def test_skip_extraction_override_parses_account_and_bank_from_account_like_recipient() -> None:
    class _Extractor:
        def __init__(self) -> None:
            self.called = False

        async def extract(self, text: str, smart_context: dict | None = None) -> TransferExtractionResult:
            self.called = True
            assert text == "Please send 5k to 816 251 1023 Access"
            assert smart_context is not None
            return TransferExtractionResult(
                entities=TransferEntities(recipient_account="8162511023", bank_name="Access Bank"),
            )

    extractor = _Extractor()
    step = ExtractionStep(user_message="Please send 5k to 816 251 1023 Access")
    payload = TransferPayload(
        recipient_name="816 251 1023 Access",
        amount=5000,
        skip_extraction=True,
    )
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=extractor,
        required_fields=[],
        previous_response=None,
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert extractor.called is True
    assert result.patch["recipient_account"] == "8162511023"
    assert result.patch["recipient_bank_name"] == "Access Bank"
    assert result.patch["skip_extraction"] is False
