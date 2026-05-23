from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.chat.src.agent.graphs.faq.models import FAQRetrievalHit
from apps.chat.src.agent.graphs.faq.nodes.gate import confidence_gate_node
from apps.chat.src.agent.graphs.faq.nodes.retrieve import create_retrieve_node
from apps.chat.src.agent.graphs.faq.nodes.validate import validate_intent_node
from apps.chat.src.agent.graphs.faq.retrieval import hybrid as hybrid_module
from apps.chat.src.agent.graphs.faq.retrieval.hybrid import HybridRetriever
from apps.chat.src.agent.orchestrator.models.domain import FAQOutcome
from shared.config.settings import settings
from shared.i18n import render_message


@pytest.mark.asyncio
async def test_retrieve_node_uses_async_session_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    class FakeSession:
        async def __aenter__(self):
            events.append("enter")
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            events.append("exit")

    session = FakeSession()

    class FakeRetriever:
        def __init__(self, db, embedding_service=None):
            assert db is session
            assert embedding_service == "embeddings"

        async def search(self, query, category=None, limit=5):
            assert query == "How do transfers work?"
            assert category == "transfers"
            assert limit == 5
            return (
                [
                    FAQRetrievalHit(
                        id="faq_1",
                        category="transfers",
                        question="How do bank transfers work?",
                        answer=f"Review and confirm before {settings.app_name} sends the transfer.",
                        score=6.0,
                        match_type="keyword",
                    )
                ],
                0.82,
            )

    monkeypatch.setattr(
        "apps.chat.src.agent.graphs.faq.nodes.retrieve.HybridRetriever",
        FakeRetriever,
    )

    node = create_retrieve_node(lambda: session, embedding_service="embeddings")
    state = await node({"message": "How do transfers work?", "detected_category": "transfers"})

    assert events == ["enter", "exit"]
    assert state["retrieval_confidence"] == 0.82
    assert state["retrieved_entries"] == [
        {
            "id": "faq_1",
            "category": "transfers",
            "question": "How do bank transfers work?",
            "answer": f"Review and confirm before {settings.app_name} sends the transfer.",
            "score": 6.0,
            "match_type": "keyword",
        }
    ]


@pytest.mark.asyncio
async def test_hybrid_retriever_keyword_match_returns_expected_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_entry = SimpleNamespace(
        id="faq_receipt",
        category="receipts",
        question="How do I get a receipt for my transfer?",
        answer=f"After an eligible successful transfer, {settings.app_name} can send a digital receipt in chat.",
    )

    class FakeFAQRepository:
        def __init__(self, db):
            del db

        async def search_by_keywords(self, keywords, limit=5, category=None):
            assert "receipt" in keywords
            assert limit == 5
            assert category is None
            return [(expected_entry, 9)]

        async def search_fuzzy(self, query_text, limit=5, category=None):
            del query_text, limit, category
            return []

    monkeypatch.setattr(hybrid_module, "FAQRepository", FakeFAQRepository)

    hits, confidence = await HybridRetriever(db=object(), embedding_service=None).search(
        "How do I get a receipt?",
        limit=5,
    )

    assert hits[0].id == "faq_receipt"
    assert hits[0].match_type == "keyword"
    assert confidence >= HybridRetriever.LOW_CONFIDENCE


def test_low_confidence_returns_localized_uncertainty_response() -> None:
    state = confidence_gate_node(
        {
            "language": "pcm",
            "retrieved_entries": [],
            "retrieval_confidence": 0.0,
        }
    )

    assert state["response"] == render_message("faq.uncertainty_response", "pcm")


def test_support_like_question_routes_to_support_handoff() -> None:
    state = validate_intent_node({"message": "I was debited but the transfer failed"})

    assert state["is_forbidden_scope"]
    assert state["should_route_to_support"]


@pytest.mark.asyncio
async def test_faq_worker_forbidden_scope_returns_support_handoff_before_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.chat.src.agent.graphs.faq import worker as worker_module

    monkeypatch.setattr(worker_module, "EmbeddingService", lambda: object())
    monkeypatch.setattr(worker_module, "capability_block_message", lambda **kwargs: None)

    def fail_get_db():
        raise AssertionError("FAQ retrieval should not run for support-scoped questions")

    faq_worker = worker_module.FAQWorker(llm=None, get_db=fail_get_db)
    result = await faq_worker.run(
        payload={},
        context={"phone_number": "2348000000000", "language": "en"},
        user_message="Why did my transfer fail?",
    )

    assert result.outcome == FAQOutcome.OK
    assert result.response == render_message("faq.support_handoff", "en")
    assert result.should_route_to_support


@pytest.mark.asyncio
async def test_faq_worker_disabled_blocks_before_db_or_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.chat.src.agent.graphs.faq import worker as worker_module

    monkeypatch.setattr(worker_module, "EmbeddingService", lambda: object())
    monkeypatch.setattr(
        worker_module,
        "capability_block_message",
        lambda **kwargs: "FAQ is temporarily unavailable.",
    )

    def fail_get_db():
        raise AssertionError("FAQ retrieval should not run when capability is disabled")

    faq_worker = worker_module.FAQWorker(llm=None, get_db=fail_get_db)
    result = await faq_worker.run(
        payload={},
        context={"phone_number": "2348000000000", "language": "en"},
        user_message="What are transfer fees?",
    )

    assert result.outcome == FAQOutcome.OK
    assert result.response == "FAQ is temporarily unavailable."
