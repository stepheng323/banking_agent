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
