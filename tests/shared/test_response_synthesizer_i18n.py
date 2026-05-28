from apps.chat.src.agent.workers.__shared__.response.context import ResponseContext
from apps.chat.src.agent.workers.__shared__.response.intent import ResponseIntent
from apps.chat.src.agent.workers.__shared__.response.synthesizer import ResponseSynthesizer


async def test_response_synthesizer_renders_keyed_template() -> None:
    synthesizer = ResponseSynthesizer()
    context = ResponseContext(intent=ResponseIntent.ASK_AMOUNT, language="en")

    response = await synthesizer.synthesize(context)

    assert response == "How much would you like to send?"


async def test_response_synthesizer_ask_recipient_no_name_variant() -> None:
    synthesizer = ResponseSynthesizer()
    context = ResponseContext(
        intent=ResponseIntent.ASK_RECIPIENT,
        language="en",
        recipient_name="recipient",
    )

    response = await synthesizer.synthesize(context)

    assert response == "Who would you like to send money to? You can provide their name, account number, or both."


async def test_response_synthesizer_candidate_defaults_are_keyed() -> None:
    synthesizer = ResponseSynthesizer()
    context = ResponseContext(
        intent=ResponseIntent.CLARIFY_BENEFICIARY,
        language="en",
        recipient_name="Alex",
        candidates=[{}],
    )

    response = await synthesizer.synthesize(context)

    assert "• Unknown (N/A • …)" in response
