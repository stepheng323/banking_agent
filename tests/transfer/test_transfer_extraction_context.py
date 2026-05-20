"""Transfer extraction context propagation tests."""

from types import SimpleNamespace
from uuid import uuid4

from apps.chat.src.agent.graphs.transfer.models.entities import TransferEntities
from apps.chat.src.agent.graphs.transfer.models.extraction import (
    Correction,
    CorrectionField,
    TransferExtractionResult,
)
from apps.chat.src.agent.graphs.transfer.models.types import TransferContext, TransferGates, TransferPayload
from apps.chat.src.agent.graphs.transfer.nodes.extraction import ExtractionStep, _extract_transfer_update
from apps.chat.src.agent.graphs.transfer.nodes.validation import ValidationStep
from apps.chat.src.agent.graphs.transfer.pipeline.base import TransferPipeline
from apps.chat.src.agent.graphs.transfer.services.extractor import TransferEntityExtractor
from apps.chat.src.agent.graphs.transfer.services.validation import ValidationService
from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome


class _CaptureExtractor:
    def __init__(self) -> None:
        self.last_user_message: str | None = None
        self.last_smart_context: dict | None = None

    async def extract(self, text: str, smart_context: dict | None = None) -> TransferExtractionResult:
        self.last_user_message = text
        self.last_smart_context = smart_context
        return TransferExtractionResult()


class _StaticExtractor:
    def __init__(self, result: TransferExtractionResult) -> None:
        self.result = result

    async def extract(self, text: str, smart_context: dict | None = None) -> TransferExtractionResult:
        del text, smart_context
        return self.result


class _RecentTransferRepository:
    def __init__(self, amount: float) -> None:
        self.amount = amount
        self.calls = 0

    async def get_recent_successful_transfer_by_recipient(self, user_id: str, recipient_hint: str) -> SimpleNamespace:
        del user_id, recipient_hint
        self.calls += 1
        return SimpleNamespace(amount=self.amount)


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


async def test_media_caption_narration_hint_fills_missing_extractor_narration() -> None:
    extractor = _StaticExtractor(
        TransferExtractionResult(
            entities=TransferEntities(
                amount=5000,
                recipient_account="8162511023",
                bank_name="OPay",
            )
        )
    )
    user_message = (
        "User caption/instruction: send 5k for groceries\n"
        "Caption-derived transfer fields: amount=5000.0.\n"
        "Caption-derived transfer fields: narration=groceries.\n\n"
        "Extracted from image: recipient_account=8162511023; bank_name=OPay."
    )

    result = await _extract_transfer_update(TransferPayload(), extractor, user_message, {})

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["amount"] == 5000
    assert result.patch["recipient_account"] == "8162511023"
    assert result.patch["recipient_bank_name"] == "OPay"
    assert result.patch["narration"] == "groceries"
    assert result.patch["authored_narration"] == "groceries"
    assert result.patch["user_note"] == "groceries"


async def test_media_caption_amount_overrides_image_receipt_amount() -> None:
    extractor = _StaticExtractor(
        TransferExtractionResult(
            entities=TransferEntities(
                amount=1500,
                recipient_account="7750145200",
                bank_name="Wema Bank",
                recipient_name="Spectranet Limited",
            )
        )
    )
    user_message = (
        "User caption/instruction: send 21k\n"
        "Caption-derived transfer fields: amount=21000.0.\n\n"
        "Extracted from image: recipient_account=7750145200; bank_name=Wema Bank; "
        "recipient_name=Spectranet Limited; amount=1500.0."
    )

    result = await _extract_transfer_update(TransferPayload(), extractor, user_message, {})

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["amount"] == 21000.0
    assert result.patch["recipient_account"] == "7750145200"
    assert result.patch["recipient_bank_name"] == "Wema Bank"
    assert result.patch["recipient_name"] == "Spectranet Limited"


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


def test_extractor_context_string_trims_beneficiary_aliases_for_compact_mode() -> None:
    extractor = TransferEntityExtractor.__new__(TransferEntityExtractor)
    context_str = extractor._build_context_string(
        {
            "required_fields": ["recipient_account"],
            "known_recipient": {"recipient_name": "Tolu"},
            "beneficiaries": [
                {"alias": "Mum"},
                {"alias": "Tolu"},
                {"alias": "Doyin"},
                {"alias": "Gaines"},
                {"alias": "Dad"},
            ],
        }
    )

    assert "Beneficiaries: Tolu, Mum, Doyin, Gaines" in context_str
    assert "Dad" not in context_str


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


async def test_extraction_step_accepts_unique_bank_label_for_beneficiary_selection() -> None:
    first_id = str(uuid4())
    second_id = str(uuid4())
    third_id = str(uuid4())
    step = ExtractionStep(user_message="the GTB")
    payload = TransferPayload(
        recipient_name="tolu",
        beneficiary_candidates=[
            {
                "index": 1,
                "beneficiary_id": first_id,
                "option_id": f"bene:{first_id}",
                "label": "Tolu Adebayo • Access Bank • ****0001",
            },
            {
                "index": 2,
                "beneficiary_id": second_id,
                "option_id": f"bene:{second_id}",
                "label": "Tolu Adeyemi • GTBank • ****0002",
            },
            {
                "index": 3,
                "beneficiary_id": third_id,
                "option_id": f"bene:{third_id}",
                "label": "Tolulope Johnson • First Bank • ****0003",
            },
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


async def test_extraction_step_does_not_select_non_unique_beneficiary_label() -> None:
    first_id = str(uuid4())
    second_id = str(uuid4())
    step = ExtractionStep(user_message="tolu")
    payload = TransferPayload(
        recipient_name="tolu",
        beneficiary_candidates=[
            {
                "index": 1,
                "beneficiary_id": first_id,
                "option_id": f"bene:{first_id}",
                "label": "Tolu Adebayo • Access Bank • ****0001",
            },
            {
                "index": 2,
                "beneficiary_id": second_id,
                "option_id": f"bene:{second_id}",
                "label": "Tolu Adeyemi • GTBank • ****0002",
            },
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
    assert result.patch == {}


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


async def test_extraction_step_accepts_source_account_bank_reference() -> None:
    step = ExtractionStep(user_message="the GTB")
    payload = TransferPayload(recipient_name="Tolu")
    context = TransferContext(
        phone_number="2348000000999",
        language="en",
        beneficiaries=[],
        accounts=[
            {
                "id": "acc-access",
                "bank_name": "Access Bank",
                "account_name": "Olamide Samuel",
                "account_number": "2010000001",
            },
            {
                "id": "acc-gtb",
                "bank_name": "GTBank",
                "account_name": "Olamide Samuel",
                "account_number": "2010000002",
            },
        ],
    )
    worker_context = SimpleNamespace(
        extractor=None,
        required_fields=["source_account_id"],
        previous_response="Which account should I use?",
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["source_account_id"] == "acc-gtb"
    assert result.patch["source_bank_name"] == "GTBank"
    assert result.patch["source_account_index"] is None
    assert result.patch["confirmation"] == {"confirmed": False}


async def test_extraction_step_accepts_source_account_suffix_reference() -> None:
    step = ExtractionStep(user_message="0003")
    payload = TransferPayload(recipient_name="Tolu")
    context = TransferContext(
        phone_number="2348000000999",
        language="en",
        beneficiaries=[],
        accounts=[
            {
                "id": "acc-first",
                "bank_name": "First Bank",
                "account_name": "Olamide Samuel",
                "account_number": "2010000003",
            }
        ],
    )
    worker_context = SimpleNamespace(
        extractor=None,
        required_fields=["source_account_id"],
        previous_response="Which account should I use?",
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["source_account_id"] == "acc-first"


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


async def test_deterministic_account_bank_fastpath() -> None:
    """When awaiting account+bank and user sends '8067892221 Opay', parse deterministically."""

    class _NeverCalledExtractor:
        def __init__(self) -> None:
            self.called = False

        async def extract(self, text: str, smart_context: dict | None = None) -> TransferExtractionResult:
            self.called = True
            return TransferExtractionResult()

    extractor = _NeverCalledExtractor()
    step = ExtractionStep(user_message="8067892221 Opay")
    payload = TransferPayload(recipient_name="Mum")
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=extractor,
        required_fields=["recipient_account", "recipient_bank_name"],
        previous_response="What's mum's account number and bank?",
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["recipient_account"] == "8067892221"
    assert result.patch["recipient_bank_name"] == "Opay"
    assert extractor.called is False


async def test_deterministic_account_bank_fastpath_bank_first() -> None:
    """When awaiting account+bank and user sends 'Opay 8162511023', parse deterministically."""

    class _NeverCalledExtractor:
        def __init__(self) -> None:
            self.called = False

        async def extract(self, text: str, smart_context: dict | None = None) -> TransferExtractionResult:
            self.called = True
            return TransferExtractionResult()

    extractor = _NeverCalledExtractor()
    step = ExtractionStep(user_message="Opay 8162511023")
    payload = TransferPayload(recipient_name="Mum")
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=extractor,
        required_fields=["recipient_account", "recipient_bank_name"],
        previous_response="What's mum's account number and bank?",
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["recipient_account"] == "8162511023"
    assert result.patch["recipient_bank_name"] == "Opay"
    assert "amount_suggestion_disabled" not in result.patch
    assert extractor.called is False


async def test_deterministic_account_bank_fastpath_bank_first_with_separators() -> None:
    """When awaiting account+bank, parse bank-first input with account separators."""

    class _NeverCalledExtractor:
        def __init__(self) -> None:
            self.called = False

        async def extract(self, text: str, smart_context: dict | None = None) -> TransferExtractionResult:
            self.called = True
            return TransferExtractionResult()

    extractor = _NeverCalledExtractor()
    step = ExtractionStep(user_message="Opay 816 251 1023")
    payload = TransferPayload(recipient_name="Mum")
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=extractor,
        required_fields=["recipient_account", "recipient_bank_name"],
        previous_response="What's mum's account number and bank?",
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["recipient_account"] == "8162511023"
    assert result.patch["recipient_bank_name"] == "Opay"
    assert extractor.called is False


async def test_deterministic_account_bank_fastpath_account_first_with_separators() -> None:
    """When awaiting account+bank, parse account-first input with account separators."""

    class _NeverCalledExtractor:
        def __init__(self) -> None:
            self.called = False

        async def extract(self, text: str, smart_context: dict | None = None) -> TransferExtractionResult:
            self.called = True
            return TransferExtractionResult()

    extractor = _NeverCalledExtractor()
    step = ExtractionStep(user_message="816-251-1023 Opay")
    payload = TransferPayload(recipient_name="Mum")
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=extractor,
        required_fields=["recipient_account", "recipient_bank_name"],
        previous_response="What's mum's account number and bank?",
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["recipient_account"] == "8162511023"
    assert result.patch["recipient_bank_name"] == "Opay"
    assert extractor.called is False


async def test_deterministic_initial_account_bank_fastpath_parses_forwarded_details_without_extractor() -> None:
    class _NeverCalledExtractor:
        def __init__(self) -> None:
            self.called = False

        async def extract(self, text: str, smart_context: dict | None = None) -> TransferExtractionResult:
            self.called = True
            return TransferExtractionResult()

    extractor = _NeverCalledExtractor()
    step = ExtractionStep(
        user_message=(
            "Account Number: 0760505261\n"
            "Account Name: ABIODUN OLATUNDE OYEBANJI\n"
            "Account Type: PREMIER SAVINGS\n"
            "Bank: Access Bank Nigeria"
        )
    )
    payload = TransferPayload()
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=extractor,
        required_fields=[],
        previous_response=None,
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["recipient_account"] == "0760505261"
    assert result.patch["recipient_bank_name"] == "Access Bank Nigeria"
    assert result.patch["amount_suggestion_disabled"] is True
    assert result.patch["confirmation"] == {"confirmed": False}
    assert extractor.called is False


async def test_deterministic_initial_account_bank_fastpath_parses_inline_details_without_extractor() -> None:
    class _NeverCalledExtractor:
        def __init__(self) -> None:
            self.called = False

        async def extract(self, text: str, smart_context: dict | None = None) -> TransferExtractionResult:
            self.called = True
            return TransferExtractionResult()

    extractor = _NeverCalledExtractor()
    step = ExtractionStep(user_message="0760505261, Opay")
    payload = TransferPayload()
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=extractor,
        required_fields=[],
        previous_response=None,
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["recipient_account"] == "0760505261"
    assert result.patch["recipient_bank_name"] == "Opay"
    assert result.patch["amount_suggestion_disabled"] is True
    assert result.patch["confirmation"] == {"confirmed": False}
    assert extractor.called is False


async def test_forwarded_bank_details_skip_recent_amount_suggestion() -> None:
    repo = _RecentTransferRepository(amount=10000)
    step = ValidationStep()
    payload = TransferPayload(
        recipient_account="0760505261",
        recipient_bank_name="Opay",
        recipient_resolved_name="TOLU ADEDAYO",
        amount_suggestion_disabled=True,
    )
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        transaction_repo=repo,
        user_id="user-1",
        validation_service=ValidationService(),
        dd_provider=None,
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.required_fields == ["amount"]
    assert result.prompt == "How much would you like to send?"
    assert result.patch == {}
    assert repo.calls == 0


async def test_deterministic_account_bank_fastpath_clears_skip_extraction_flag() -> None:
    """Account+bank deterministic path should clear skip_extraction for next user turn."""

    class _NeverCalledExtractor:
        def __init__(self) -> None:
            self.called = False

        async def extract(self, text: str, smart_context: dict | None = None) -> TransferExtractionResult:
            self.called = True
            return TransferExtractionResult()

    extractor = _NeverCalledExtractor()
    step = ExtractionStep(user_message="Opay 8162511023")
    payload = TransferPayload(recipient_name="8162511023 Opay", skip_extraction=True)
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=extractor,
        required_fields=["recipient_account", "recipient_bank_name"],
        previous_response="What's mum's account number and bank?",
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["recipient_account"] == "8162511023"
    assert result.patch["recipient_bank_name"] == "Opay"
    assert result.patch["skip_extraction"] is False
    assert extractor.called is False


async def test_deterministic_amount_fastpath_parses_shorthand_reply() -> None:
    """When awaiting amount, shorthand replies like '20k' should parse deterministically."""
    extractor = _CaptureExtractor()
    step = ExtractionStep(user_message="20k")
    payload = TransferPayload(recipient_name="Mum")
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=extractor,
        required_fields=["amount"],
        previous_response="How much would you like to send?",
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["amount"] == 20000
    assert result.patch["suggested_amount"] is None
    assert extractor.last_user_message is None


async def test_deterministic_amount_fastpath_parses_send_amount_reply() -> None:
    extractor = _CaptureExtractor()
    pipeline = TransferPipeline([ExtractionStep(user_message="Send 5k")])
    payload = TransferPayload(recipient_account="0760505261", recipient_bank_name="Opay")
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=extractor,
        required_fields=["amount"],
        previous_response="How much would you like to send?",
    )

    result = await pipeline.run(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["amount"] == 5000
    assert result.patch["recipient_account"] == "0760505261"
    assert result.patch["recipient_bank_name"] == "Opay"
    assert extractor.last_user_message is None


async def test_deterministic_simple_transfer_fastpath_parses_name_without_extractor() -> None:
    extractor = _CaptureExtractor()
    step = ExtractionStep(user_message="Send 5k to Mum")
    payload = TransferPayload()
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=extractor,
        required_fields=[],
        previous_response=None,
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["amount"] == 5000
    assert result.patch["recipient_name"] == "Mum"
    assert result.patch["transfer_percentage"] is None
    assert result.patch["transfer_all"] is False
    assert extractor.last_user_message is None


async def test_deterministic_simple_transfer_fastpath_parses_account_without_extractor() -> None:
    extractor = _CaptureExtractor()
    step = ExtractionStep(user_message="Send 5k to 8162511023")
    payload = TransferPayload()
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=extractor,
        required_fields=[],
        previous_response=None,
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["amount"] == 5000
    assert result.patch["recipient_account"] == "8162511023"
    assert "recipient_name" not in result.patch
    assert extractor.last_user_message is None


async def test_deterministic_simple_transfer_fastpath_skips_batch_turns() -> None:
    extractor = _CaptureExtractor()
    step = ExtractionStep(user_message="send 10k each to mum, tolu and doyin")
    payload = TransferPayload()
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=extractor,
        required_fields=[],
        previous_response=None,
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert "amount" not in result.patch
    assert extractor.last_user_message == "send 10k each to mum, tolu and doyin"


async def test_narration_correction_updates_narration_and_user_note() -> None:
    class _NarrationCorrectionExtractor:
        async def extract(self, text: str, smart_context: dict | None = None) -> TransferExtractionResult:
            del text, smart_context
            return TransferExtractionResult(
                correction=Correction(field=CorrectionField.NARRATION, new_value="groceries"),
                acknowledgment="Updated narration.",
            )

    step = ExtractionStep(user_message="it's for groceries")
    payload = TransferPayload(recipient_name="Tolu", recipient_resolved_name="Tolu")
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=_NarrationCorrectionExtractor(),
        required_fields=[],
        previous_response=None,
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["authored_narration"] == "groceries"
    assert result.patch["narration"] == "groceries"
    assert result.patch["user_note"] == "groceries"
    assert result.patch["transition_acknowledgment"] == "Updated narration."


async def test_amount_update_does_not_clear_recipient_binding_when_name_matches_resolved_identity() -> None:
    class _AmountCorrectionExtractor:
        async def extract(self, text: str, smart_context: dict | None = None) -> TransferExtractionResult:
            del text, smart_context
            return TransferExtractionResult(
                entities=TransferEntities(amount=20000, recipient_name="Mercy Johnson"),
                correction=Correction(field=CorrectionField.AMOUNT, new_value=20000),
                acknowledgment="Updated amount.",
            )

    step = ExtractionStep(user_message="Change amount to 20k")
    payload = TransferPayload(
        recipient_name="Mum",
        recipient_resolved_name="Mercy Johnson",
        recipient_account="8162511023",
        recipient_bank_name="Opay",
        recipient_bank_code="100004",
        beneficiary_id="bene-1",
    )
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=_AmountCorrectionExtractor(),
        required_fields=[],
        previous_response=None,
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["amount"] == 20000
    assert result.patch["recipient_name"] == "Mercy Johnson"
    assert "recipient_account" not in result.patch
    assert "recipient_bank_name" not in result.patch
    assert "beneficiary_id" not in result.patch


async def test_fanout_bound_confirmation_edit_keeps_recipient_binding_on_narration_update() -> None:
    class _FanoutNarrationExtractor:
        async def extract(self, text: str, smart_context: dict | None = None) -> TransferExtractionResult:
            del text, smart_context
            return TransferExtractionResult(
                entities=TransferEntities(recipient_name="Mum", narration="allowance"),
                acknowledgment="Updated narration.",
            )

    step = ExtractionStep(user_message="The transfer is for allowance")
    payload = TransferPayload(
        recipient_name="Tolu",
        recipient_resolved_name="Tolu Adedayo",
        recipient_account="0760505261",
        recipient_bank_name="First Bank",
        recipient_bank_code="011",
        recipient_binding_source="fanout",
        recipient_binding_index=2,
        previous_confirmation_snapshot={"amount": 10000, "recipient_name": "Tolu"},
    )
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=_FanoutNarrationExtractor(),
        required_fields=[],
        previous_response=None,
        confirmation_task_count=2,
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["authored_narration"] == "allowance"
    assert result.patch["narration"] == "allowance"
    assert result.patch["transition_acknowledgment"] == "Updated narration."
    assert "recipient_name" not in result.patch
    assert "recipient_resolved_name" not in result.patch
    assert "recipient_bank_name" not in result.patch
    assert "recipient_bank_code" not in result.patch
    assert "description" not in result.patch


async def test_amount_update_does_not_clear_binding_for_combined_alias_and_resolved_name() -> None:
    class _AmountCorrectionExtractor:
        async def extract(self, text: str, smart_context: dict | None = None) -> TransferExtractionResult:
            del text, smart_context
            return TransferExtractionResult(
                entities=TransferEntities(amount=10000, recipient_name="Mum (Mercy Johnson)"),
                correction=Correction(field=CorrectionField.AMOUNT, new_value=10000),
                acknowledgment="Updated amount.",
            )

    step = ExtractionStep(user_message="Change amount to 10k")
    payload = TransferPayload(
        recipient_name="Mum",
        recipient_resolved_name="Mercy Johnson",
        recipient_account="8162511023",
        recipient_bank_name="Opay",
        recipient_bank_code="100004",
        beneficiary_id="bene-1",
    )
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=_AmountCorrectionExtractor(),
        required_fields=[],
        previous_response=None,
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["amount"] == 10000
    assert result.patch["recipient_name"] == "Mum (Mercy Johnson)"
    assert "recipient_account" not in result.patch
    assert "recipient_bank_name" not in result.patch
    assert "beneficiary_id" not in result.patch


async def test_amount_update_clears_percentage_and_transfer_all_flags() -> None:
    class _AmountCorrectionExtractor:
        async def extract(self, text: str, smart_context: dict | None = None) -> TransferExtractionResult:
            del text, smart_context
            return TransferExtractionResult(
                entities=TransferEntities(amount=10000),
                correction=Correction(field=CorrectionField.AMOUNT, new_value=10000),
                acknowledgment="Updated amount.",
            )

    step = ExtractionStep(user_message="Change amount to 10k")
    payload = TransferPayload(
        recipient_name="Mum",
        transfer_percentage=50,
        transfer_all=True,
    )
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=_AmountCorrectionExtractor(),
        required_fields=[],
        previous_response=None,
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["amount"] == 10000
    assert result.patch["transfer_percentage"] is None
    assert result.patch["transfer_all"] is False


async def test_percentage_update_clears_stale_amount_for_account_aware_resolution() -> None:
    class _PercentageExtractor:
        async def extract(self, text: str, smart_context: dict | None = None) -> TransferExtractionResult:
            del text, smart_context
            return TransferExtractionResult(
                entities=TransferEntities(transfer_percentage=50, source_bank_name="First Bank"),
            )

    step = ExtractionStep(user_message="Use half from my first bank")
    payload = TransferPayload(
        recipient_name="Mum",
        amount=20000,
        source_account_id="acc-1",
        source_bank_name="Zenith Bank",
    )
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=_PercentageExtractor(),
        required_fields=[],
        previous_response=None,
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["transfer_percentage"] == 50
    assert result.patch["source_bank_name"] == "First Bank"
    assert result.patch["amount"] is None


async def test_account_then_amount_turns_do_not_get_stuck_due_to_skip_extraction() -> None:
    """Regression: after account+bank deterministic parse, next amount turn should still extract."""
    extractor = _CaptureExtractor()
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    payload = TransferPayload(recipient_name="8162511023 Opay", skip_extraction=True)

    account_step = ExtractionStep(user_message="Opay 8162511023")
    account_ctx = SimpleNamespace(
        extractor=extractor,
        required_fields=["recipient_account", "recipient_bank_name"],
        previous_response="What's mum's account number and bank?",
    )
    account_result = await account_step.execute(payload, context, TransferGates(), account_ctx)

    next_payload = payload.model_copy(update=account_result.patch)
    amount_step = ExtractionStep(user_message="20k")
    amount_ctx = SimpleNamespace(
        extractor=extractor,
        required_fields=["amount"],
        previous_response="How much would you like to send?",
    )
    amount_result = await amount_step.execute(next_payload, context, TransferGates(), amount_ctx)

    assert account_result.outcome == TransactionOutcome.OK
    assert account_result.patch["recipient_account"] == "8162511023"
    assert account_result.patch["recipient_bank_name"] == "Opay"
    assert account_result.patch["skip_extraction"] is False
    assert amount_result.outcome == TransactionOutcome.OK
    assert amount_result.patch["amount"] == 20000


async def test_deterministic_fastpath_invalid_normalized_account_falls_back_to_extractor() -> None:
    """Invalid normalized account length should skip deterministic fast-path."""
    extractor = _CaptureExtractor()
    step = ExtractionStep(user_message="Opay 81625110234")
    payload = TransferPayload(recipient_name="Mum")
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=extractor,
        required_fields=["recipient_account", "recipient_bank_name"],
        previous_response="What's mum's account number and bank?",
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert "recipient_account" not in result.patch
    assert extractor.last_user_message == "Opay 81625110234"


async def test_deterministic_fastpath_rejects_numeric_bank_tail() -> None:
    """Numeric-only bank tail should skip deterministic fast-path and call extractor."""
    extractor = _CaptureExtractor()
    step = ExtractionStep(user_message="8162511023 12345")
    payload = TransferPayload(recipient_name="Mum")
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=extractor,
        required_fields=["recipient_account", "recipient_bank_name"],
        previous_response="What's mum's account number and bank?",
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert "recipient_account" not in result.patch
    assert extractor.last_user_message == "8162511023 12345"


async def test_deterministic_fastpath_does_not_trigger_for_pure_digits() -> None:
    """The fast-path requires a non-digit bank token; pure digit strings should fall through to LLM."""
    extractor = _CaptureExtractor()
    step = ExtractionStep(user_message="8067892221")
    payload = TransferPayload(recipient_name="Mum")
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=extractor,
        required_fields=["recipient_account", "recipient_bank_name"],
        previous_response="What's mum's account number and bank?",
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)
    assert result.outcome == TransactionOutcome.OK
    assert extractor.last_user_message == "8067892221"


async def test_single_confirmation_amount_edit_fastpath_skips_extractor() -> None:
    class _NeverCalledExtractor:
        def __init__(self) -> None:
            self.called = False

        async def extract(self, text: str, smart_context: dict | None = None) -> TransferExtractionResult:
            self.called = True
            return TransferExtractionResult()

    extractor = _NeverCalledExtractor()
    step = ExtractionStep(user_message="make it 20k")
    payload = TransferPayload(
        amount=10000,
        recipient_name="mum",
        recipient_resolved_name="Mercy Johnson",
        recipient_account="8162511023",
        recipient_bank_name="Opay",
        previous_confirmation_snapshot={
            "amount": 10000,
            "recipient_name": "mum (Mercy Johnson)",
            "recipient_bank": "Opay",
            "recipient_account": "8162511023",
        },
    )
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=extractor,
        required_fields=[],
        previous_response=None,
        confirmation_task_count=1,
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["amount"] == 20000
    assert result.patch["funding_plan"] is None
    assert result.patch["transfer_percentage"] is None
    assert result.patch["transfer_all"] is False
    assert result.patch["transition_acknowledgment"] == "Changing amount to 20k."
    assert extractor.called is False


async def test_single_confirmation_transfer_all_fastpath_skips_extractor() -> None:
    class _NeverCalledExtractor:
        def __init__(self) -> None:
            self.called = False

        async def extract(self, text: str, smart_context: dict | None = None) -> TransferExtractionResult:
            self.called = True
            return TransferExtractionResult()

    extractor = _NeverCalledExtractor()
    step = ExtractionStep(user_message="send all")
    payload = TransferPayload(
        amount=10000,
        recipient_name="mum",
        previous_confirmation_snapshot={"amount": 10000, "recipient_name": "mum"},
    )
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=extractor,
        required_fields=[],
        previous_response=None,
        confirmation_task_count=1,
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["amount"] is None
    assert result.patch["transfer_all"] is True
    assert result.patch["transfer_percentage"] is None
    assert extractor.called is False


async def test_single_confirmation_percentage_fastpath_skips_extractor() -> None:
    class _NeverCalledExtractor:
        def __init__(self) -> None:
            self.called = False

        async def extract(self, text: str, smart_context: dict | None = None) -> TransferExtractionResult:
            self.called = True
            return TransferExtractionResult()

    extractor = _NeverCalledExtractor()
    step = ExtractionStep(user_message="half")
    payload = TransferPayload(
        amount=10000,
        recipient_name="mum",
        previous_confirmation_snapshot={"amount": 10000, "recipient_name": "mum"},
    )
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=extractor,
        required_fields=[],
        previous_response=None,
        confirmation_task_count=1,
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["amount"] is None
    assert result.patch["transfer_percentage"] == 50.0
    assert result.patch["transfer_all"] is False
    assert extractor.called is False


async def test_single_confirmation_source_bank_switch_fastpath_skips_extractor() -> None:
    class _NeverCalledExtractor:
        def __init__(self) -> None:
            self.called = False

        async def extract(self, text: str, smart_context: dict | None = None) -> TransferExtractionResult:
            self.called = True
            return TransferExtractionResult()

    extractor = _NeverCalledExtractor()
    step = ExtractionStep(user_message="use first bank instead")
    payload = TransferPayload(
        amount=10000,
        recipient_name="mum",
        source_bank_name="Zenith Bank",
        source_account_id="acc-zenith",
        source_account_name="Olamide Samuel",
        source_account_number="1234509384",
        previous_confirmation_snapshot={"amount": 10000, "recipient_name": "mum"},
    )
    context = TransferContext(
        phone_number="2348000000999",
        language="en",
        beneficiaries=[],
        accounts=[
            {"id": "acc-zenith", "bank_name": "Zenith Bank", "account_number": "1234509384"},
            {"id": "acc-first", "bank_name": "First Bank", "account_number": "2222333344"},
        ],
    )
    worker_context = SimpleNamespace(
        extractor=extractor,
        required_fields=[],
        previous_response=None,
        confirmation_task_count=1,
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["source_bank_name"] == "First Bank"
    assert result.patch["source_account_id"] is None
    assert result.patch["source_account_name"] is None
    assert result.patch["source_account_number"] is None
    assert result.patch["source_affinity_mode"] == "explicit"
    assert extractor.called is False


async def test_single_confirmation_narration_fastpath_skips_extractor() -> None:
    class _NeverCalledExtractor:
        def __init__(self) -> None:
            self.called = False

        async def extract(self, text: str, smart_context: dict | None = None) -> TransferExtractionResult:
            self.called = True
            return TransferExtractionResult()

    extractor = _NeverCalledExtractor()
    step = ExtractionStep(user_message="add that its for march salary")
    payload = TransferPayload(
        amount=30000,
        recipient_name="mum",
        previous_confirmation_snapshot={"amount": 30000, "recipient_name": "mum"},
    )
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=extractor,
        required_fields=[],
        previous_response=None,
        confirmation_task_count=1,
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["authored_narration"] == "march salary"
    assert result.patch["narration"] == "march salary"
    assert result.patch["user_note"] == "march salary"
    assert result.patch["transition_acknowledgment"] == "Added narration."
    assert extractor.called is False


async def test_confirmed_resume_skips_extractor_for_resolved_transfer() -> None:
    class _NeverCalledExtractor:
        def __init__(self) -> None:
            self.called = False

        async def extract(self, text: str, smart_context: dict | None = None) -> TransferExtractionResult:
            self.called = True
            return TransferExtractionResult()

    extractor = _NeverCalledExtractor()
    step = ExtractionStep(user_message="Send 30000.0 to mum")
    payload = TransferPayload(
        amount=30000,
        recipient_name="mum",
        recipient_account="8162511023",
        recipient_bank_name="Opay",
        source_account_id="acc-zenith",
        source_bank_name="Zenith Bank",
        confirmation={"confirmed": True},
    )
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=extractor,
        required_fields=[],
        previous_response=None,
    )

    result = await step.execute(
        payload,
        context,
        TransferGates(pin_verified=True, confirmation_confirmed=True),
        worker_context,
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch == {}
    assert extractor.called is False


async def test_confirmation_edit_fastpath_does_not_trigger_for_multi_task_confirmation() -> None:
    extractor = _CaptureExtractor()
    step = ExtractionStep(user_message="make it 20k")
    payload = TransferPayload(
        amount=10000,
        recipient_name="mum",
        previous_confirmation_snapshot={"amount": 10000, "recipient_name": "mum"},
    )
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=extractor,
        required_fields=[],
        previous_response=None,
        confirmation_task_count=2,
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert "amount" not in result.patch
    assert extractor.last_user_message == "make it 20k"


async def test_scoped_multi_task_confirmation_narration_fastpath_skips_extractor() -> None:
    class _NeverCalledExtractor:
        def __init__(self) -> None:
            self.called = False

        async def extract(self, text: str, smart_context: dict | None = None) -> TransferExtractionResult:
            self.called = True
            return TransferExtractionResult()

    extractor = _NeverCalledExtractor()
    step = ExtractionStep(user_message="for transport")
    payload = TransferPayload(
        amount=10000,
        recipient_name="tolu",
        previous_confirmation_snapshot={"amount": 10000, "recipient_name": "tolu"},
        confirmation_message_scoped=True,
    )
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=extractor,
        required_fields=[],
        previous_response=None,
        confirmation_task_count=2,
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert result.patch["authored_narration"] == "transport"
    assert result.patch["narration"] == "transport"
    assert result.patch["user_note"] == "transport"
    assert result.patch["confirmation_message_scoped"] is False
    assert "description" not in result.patch
    assert extractor.called is False


async def test_fanout_bound_recipient_misparse_does_not_leave_stale_description() -> None:
    class _RecipientMisparseExtractor:
        async def extract(self, text: str, smart_context: dict | None = None) -> TransferExtractionResult:
            del text, smart_context
            return TransferExtractionResult(
                entities=TransferEntities(recipient_name="Transport"),
                acknowledgment="Updated narration.",
            )

    step = ExtractionStep(user_message="for transport")
    payload = TransferPayload(
        recipient_name="Tolu",
        recipient_resolved_name="Tolu Adedayo",
        recipient_account="0760505261",
        recipient_bank_name="First Bank",
        recipient_bank_code="011",
        recipient_binding_source="fanout",
        recipient_binding_index=2,
        previous_confirmation_snapshot={"amount": 10000, "recipient_name": "Tolu"},
    )
    context = TransferContext(phone_number="2348000000999", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        extractor=_RecipientMisparseExtractor(),
        required_fields=[],
        previous_response=None,
        confirmation_task_count=2,
    )

    result = await step.execute(payload, context, TransferGates(), worker_context)

    assert result.outcome == TransactionOutcome.OK
    assert "recipient_name" not in result.patch
    assert "recipient_bank_name" not in result.patch
    assert "description" not in result.patch
