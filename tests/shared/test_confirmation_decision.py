from typing import Any

import pytest

from apps.chat.src.agent.orchestrator.confirmation.affirmation.service import AffirmationService
from apps.chat.src.agent.orchestrator.confirmation.confirmation_classifier import (
    classify_confirmation_reply,
    classify_confirmation_reply_sync,
)
from apps.chat.src.agent.orchestrator.confirmation.confirmation_models import ConfirmationDecisionOutput
from shared.i18n.models import LocaleCode


class _StructuredConfirmationLLM:
    def __init__(self, output: dict[str, Any] | ConfirmationDecisionOutput) -> None:
        self.output = output

    async def ainvoke(self, _messages: list[dict[str, str]]) -> dict[str, Any] | ConfirmationDecisionOutput:
        return self.output


@pytest.mark.parametrize(
    ("locale", "text"),
    [
        (LocaleCode.EN, "yes please"),
        (LocaleCode.PCM, "yes na"),
        (LocaleCode.YO, "beeni"),
        (LocaleCode.HA, "na'am"),
        (LocaleCode.IG, "ee"),
    ],
)
def test_confirmation_decision_fastpath_approves_supported_locales(locale: LocaleCode, text: str) -> None:
    decision = classify_confirmation_reply_sync(text, prompt_kind="transaction_confirmation", locale=locale)

    assert decision.action == "approve"
    assert decision.source == "fastpath"


def test_confirmation_decision_fastpath_scans_safe_locale_fallbacks() -> None:
    english_reply_in_pidgin_context = classify_confirmation_reply_sync(
        "yes please",
        prompt_kind="resume_prompt",
        locale=LocaleCode.PCM,
    )
    yoruba_reply_without_locale = classify_confirmation_reply_sync("beeni", prompt_kind="resume_prompt", locale=None)

    assert english_reply_in_pidgin_context.action == "approve"
    assert yoruba_reply_without_locale.action == "approve"


@pytest.mark.parametrize(
    ("locale", "text"),
    [
        (LocaleCode.EN, "no thanks"),
        (LocaleCode.PCM, "no abeg"),
        (LocaleCode.YO, "rara"),
        (LocaleCode.HA, "ba yanzu ba"),
        (LocaleCode.IG, "mba"),
    ],
)
def test_confirmation_decision_fastpath_rejects_supported_locales(locale: LocaleCode, text: str) -> None:
    decision = classify_confirmation_reply_sync(text, prompt_kind="transaction_confirmation", locale=locale)

    assert decision.action == "reject"
    assert decision.source == "fastpath"


@pytest.mark.asyncio
async def test_confirmation_decision_llm_can_approve_safe_resume_reply() -> None:
    decision = await classify_confirmation_reply(
        "make we continue that one",
        prompt_kind="resume_prompt",
        locale="pcm",
        context="We asked whether to continue a stashed transfer.",
        structured_llm=_StructuredConfirmationLLM(
            {"action": "approve", "confidence": 0.94, "reason": "clear_resume_acceptance"}
        ),
    )

    assert decision.action == "approve"
    assert decision.source == "llm"


@pytest.mark.asyncio
async def test_confirmation_decision_llm_can_reject_safe_resume_reply() -> None:
    decision = await classify_confirmation_reply(
        "nah leave am first",
        prompt_kind="resume_prompt",
        locale="pcm",
        context="We asked whether to continue a stashed transfer.",
        structured_llm=_StructuredConfirmationLLM(
            {"action": "reject", "confidence": 0.9, "reason": "clear_resume_rejection"}
        ),
    )

    assert decision.action == "reject"
    assert decision.source == "llm"


@pytest.mark.parametrize(
    ("text", "expected_action"),
    [
        ("send 5k to Ada", "new_request"),
        ("make it 10k", "modify"),
        ("add it for feeding", "modify"),
        ("use 0123456789 instead", "modify"),
    ],
)
def test_confirmation_decision_guardrails_block_approval(text: str, expected_action: str) -> None:
    decision = classify_confirmation_reply_sync(text, prompt_kind="resume_prompt", locale="en")

    assert decision.action == expected_action
    assert decision.source == "guardrail"


def test_legacy_affirmation_service_no_longer_approves_broad_substrings() -> None:
    assert AffirmationService.classify_sync("send").is_unclear
    assert AffirmationService.classify_sync("abeg").is_unclear
