import pytest

from shared.services.unsupported_capability_detection import detect_unsupported_capability
from shared.services.unsupported_capability_models import (
    UnsupportedBoundaryTurnOutput,
    UnsupportedCapabilitySemanticOutput,
)
from shared.services.unsupported_capability_presentation import unsupported_capability_params
from shared.services.unsupported_capability_registry import get_unsupported_capability
from shared.services.unsupported_capability_semantic import (
    classify_unsupported_boundary_turn_semantic,
    classify_unsupported_capability_semantic,
    unsupported_boundary_turn_messages,
    unsupported_capability_semantic_messages,
    validate_semantic_unsupported_capability,
    validate_unsupported_boundary_turn,
)


class _FakeStructuredLLM:
    def __init__(self, output: object) -> None:
        self.output = output
        self.messages: list[dict[str, str]] | None = None

    async def ainvoke(self, messages: list[dict[str, str]]) -> object:
        self.messages = messages
        return self.output


@pytest.mark.parametrize(
    ("text", "expected_key"),
    [
        ("Can you borrow me money?", "lending"),
        ("Buy bitcoin for me", "investments"),
        ("What stock should I buy?", "financial_advice"),
        ("Can you send money abroad?", "international_transfers"),
        ("Export my statement as PDF", "pdf_exports"),
        ("Download CSV for my transactions", "csv_exports"),
        ("Export statement as CSV", "csv_exports"),
        ("Show my all-time transaction history", "all_time_history"),
    ],
)
def test_unsupported_capability_registry_detects_common_boundaries(text: str, expected_key: str) -> None:
    capability = detect_unsupported_capability(text)

    assert capability is not None
    assert capability.key == expected_key


@pytest.mark.parametrize(
    "text",
    [
        "send 5k to Ada",
        "buy data for me",
        "what is my access balance",
        "show my recent transactions",
        "show scheduled transactions",
    ],
)
def test_unsupported_capability_registry_ignores_supported_banking_requests(text: str) -> None:
    assert detect_unsupported_capability(text) is None


@pytest.mark.parametrize(
    ("text", "expected_key"),
    [
        ("ra bitcoin fun mi", "investments"),
        ("zuba jari a crypto", "investments"),
        ("zuta bitcoin for me", "investments"),
        ("ya mi lowo", "lending"),
        ("ina bukatar lamuni", "lending"),
        ("binye m ego", "lending"),
        ("Wane stock zan saya?", "financial_advice"),
        ("gini ka m zuta in crypto?", "financial_advice"),
        ("aika kudi zuwa waje", "international_transfers"),
        ("zipu ego mba ofesi", "international_transfers"),
        ("gbe statement jade as PDF", "pdf_exports"),
        ("sauke transaction as CSV", "csv_exports"),
        ("gbogbo transactions mi", "all_time_history"),
        ("transaction niile", "all_time_history"),
    ],
)
def test_unsupported_capability_registry_detects_localized_boundaries(
    text: str,
    expected_key: str,
) -> None:
    capability = detect_unsupported_capability(text)

    assert capability is not None
    assert capability.key == expected_key


def test_unsupported_capability_params_are_locale_aware() -> None:
    capability = get_unsupported_capability("lending")
    assert capability is not None

    params = unsupported_capability_params(capability, locale="yo")

    assert params["capability"] == "awin tabi loan"
    assert params["supported"] == "transfer, airtime/data, balance, ati wiwa transaction"


def test_semantic_unsupported_validation_accepts_known_high_confidence_key() -> None:
    capability = validate_semantic_unsupported_capability(
        UnsupportedCapabilitySemanticOutput(
            action="unsupported",
            capability_key="investments",
            confidence=0.91,
            reason="semantic_match",
        )
    )

    assert capability is not None
    assert capability.key == "investments"


@pytest.mark.parametrize(
    "decision",
    [
        UnsupportedCapabilitySemanticOutput(
            action="unsupported",
            capability_key="unknown_capability",
            confidence=0.95,
            reason="unknown_key",
        ),
        UnsupportedCapabilitySemanticOutput(
            action="unsupported",
            capability_key="investments",
            confidence=0.40,
            reason="low_confidence",
        ),
        UnsupportedCapabilitySemanticOutput(
            action="supported_or_other",
            capability_key="investments",
            confidence=0.99,
            reason="supported",
        ),
    ],
)
def test_semantic_unsupported_validation_rejects_unsafe_outputs(
    decision: UnsupportedCapabilitySemanticOutput,
) -> None:
    assert validate_semantic_unsupported_capability(decision) is None


def test_boundary_turn_validation_accepts_same_topic_without_requiring_user_keyword() -> None:
    decision = validate_unsupported_boundary_turn(
        UnsupportedBoundaryTurnOutput(
            action="same_unsupported",
            capability_key=None,
            confidence=0.88,
            reason="repayment_offer_continues_lending_thread",
        ),
        boundary_key="lending",
    )

    assert decision is not None
    assert decision.action == "same_unsupported"
    assert decision.capability_key == "lending"


@pytest.mark.parametrize(
    "decision",
    [
        UnsupportedBoundaryTurnOutput(
            action="same_unsupported",
            capability_key="investments",
            confidence=0.9,
            reason="wrong_boundary",
        ),
        UnsupportedBoundaryTurnOutput(
            action="same_unsupported",
            capability_key="lending",
            confidence=0.2,
            reason="low_confidence",
        ),
        UnsupportedBoundaryTurnOutput(
            action="new_unsupported",
            capability_key="unknown",
            confidence=0.93,
            reason="unknown_key",
        ),
    ],
)
def test_boundary_turn_validation_rejects_unsafe_outputs(decision: UnsupportedBoundaryTurnOutput) -> None:
    assert validate_unsupported_boundary_turn(decision, boundary_key="lending") is None


def test_semantic_unsupported_prompt_is_registry_bounded() -> None:
    messages = unsupported_capability_semantic_messages(
        text="help me grow my money in stocks",
        locale="en",
    )

    assert "Do not invent categories" in messages[0]["content"]
    assert "investments" in messages[0]["content"]
    assert "User message" in messages[1]["content"]


def test_boundary_turn_prompt_is_boundary_scoped() -> None:
    messages = unsupported_boundary_turn_messages(
        text="I will pay back",
        boundary_key="lending",
        boundary_label="loans or lending",
        followup_count=1,
        locale="en",
    )

    assert "Active unsupported boundary: lending" in messages[0]["content"]
    assert "offers repayment" in messages[0]["content"]
    assert "I will pay back" in messages[1]["content"]


@pytest.mark.asyncio
async def test_semantic_unsupported_classifier_uses_structured_llm() -> None:
    llm = _FakeStructuredLLM(
        UnsupportedCapabilitySemanticOutput(
            action="unsupported",
            capability_key="investments",
            confidence=0.92,
            reason="semantic_match",
        )
    )

    output = await classify_unsupported_capability_semantic(
        "help me grow my money in stocks",
        structured_llm=llm,
    )

    assert output.capability_key == "investments"
    assert llm.messages is not None
    assert "help me grow my money in stocks" in llm.messages[1]["content"]


@pytest.mark.asyncio
async def test_boundary_turn_classifier_uses_structured_llm() -> None:
    llm = _FakeStructuredLLM(
        UnsupportedBoundaryTurnOutput(
            action="same_unsupported",
            capability_key="lending",
            confidence=0.91,
            reason="repayment_offer",
        )
    )

    output = await classify_unsupported_boundary_turn_semantic(
        "I will pay back",
        boundary_key="lending",
        boundary_label="loans or lending",
        followup_count=1,
        structured_llm=llm,
    )

    assert output.action == "same_unsupported"
    assert output.capability_key == "lending"
    assert llm.messages is not None
    assert "I will pay back" in llm.messages[1]["content"]
