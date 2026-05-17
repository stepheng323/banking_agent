from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.chat.src.agent.graphs.support.classifier import SupportClassifier
from apps.chat.src.agent.graphs.support.models import SupportIntent


class _LLMStub:
    def __init__(self, content: str | None = None, *, error: Exception | None = None) -> None:
        self.content = content
        self.error = error

    async def ainvoke(self, prompt: str):
        del prompt
        if self.error is not None:
            raise self.error
        return SimpleNamespace(content=self.content or "{}")


@pytest.mark.asyncio
async def test_support_classifier_prefers_structured_llm_over_regex_fallback() -> None:
    classifier = SupportClassifier(
        _LLMStub(
            '{"intent":"receipt_request","confidence":0.91,'
            '"transaction_ref":{"amount":null,"recipient_name":null,"date_hint":null}}'
        )
    )

    result = await classifier.classify("My last transaction failed")

    assert result.intent == SupportIntent.RECEIPT_REQUEST
    assert result.transaction_ref is None


@pytest.mark.asyncio
async def test_support_classifier_falls_back_when_llm_fails() -> None:
    classifier = SupportClassifier(_LLMStub(error=RuntimeError("model unavailable")))

    result = await classifier.classify("My last transaction failed")

    assert result.intent == SupportIntent.FAILED_TRANSFER
    assert result.transaction_ref is not None
    assert result.transaction_ref.use_recent is True


@pytest.mark.asyncio
async def test_support_classifier_falls_back_for_empty_llm_output() -> None:
    classifier = SupportClassifier(_LLMStub("{}"))

    result = await classifier.classify("My last transaction failed")

    assert result.intent == SupportIntent.FAILED_TRANSFER
    assert result.transaction_ref is not None
    assert result.transaction_ref.use_recent is True


@pytest.mark.asyncio
async def test_support_classifier_does_not_regex_override_explicit_null_intent() -> None:
    classifier = SupportClassifier(
        _LLMStub(
            '{"intent":null,"confidence":0.93,'
            '"transaction_ref":{"amount":null,"recipient_name":null,"date_hint":null}}'
        )
    )

    result = await classifier.classify("Okay great, I thought it failed")

    assert result.intent is None
    assert result.confidence == 0.93


@pytest.mark.asyncio
async def test_support_classifier_low_confidence_structured_intent_is_not_support_intent() -> None:
    classifier = SupportClassifier(
        _LLMStub(
            '{"intent":"failed_transfer","confidence":0.2,'
            '"transaction_ref":{"amount":null,"recipient_name":null,"date_hint":null}}'
        )
    )

    result = await classifier.classify("My last transaction failed")

    assert result.intent == SupportIntent.FAILED_TRANSFER
    assert result.confidence == 0.2
    assert classifier.is_support_intent(result) is False


@pytest.mark.asyncio
async def test_support_classifier_falls_back_for_debited_recipient_did_not_receive() -> None:
    classifier = SupportClassifier(_LLMStub(error=RuntimeError("model unavailable")))

    result = await classifier.classify("I was debited but they didn't receive it")

    assert result.intent == SupportIntent.WRONG_DEBIT
    assert result.transaction_ref is not None
    assert result.transaction_ref.use_recent is False
