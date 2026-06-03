from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.chat.src.agent.orchestrator.models.domain import FAQOutcome
from banking.faq.models import FAQRetrievalHit
from banking.faq.nodes.gate import confidence_gate_node
from banking.faq.nodes.guard import final_guard_node
from banking.faq.nodes.retrieve import create_retrieve_node
from banking.faq.nodes.validate import validate_intent_node
from banking.faq.retrieval import hybrid as hybrid_module
from banking.faq.retrieval.hybrid import HybridRetriever
from banking.presentation.i18n.renderer import render_message
from shared.config.settings import settings


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
            assert query == "transfer work"
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
        "banking.faq.nodes.retrieve.HybridRetriever",
        FakeRetriever,
    )

    node = create_retrieve_node(lambda: session, embedding_service="embeddings")
    state = await node(
        {
            "message": "How do transfers work?",
            "normalized_query": "transfer work",
            "detected_category": "transfers",
        }
    )

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


def test_final_guard_replaces_unsafe_faq_response() -> None:
    state = final_guard_node(
        {
            "language": "en",
            "response": "I can see that your transaction failed.",
        }
    )

    assert state["response"] == render_message("faq.uncertainty_response", "en")
    assert state["response_source"] == "guard_fallback"
    assert state["error"] == "unsafe_faq_response"


@pytest.mark.asyncio
async def test_faq_worker_forbidden_scope_returns_support_handoff_before_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from banking.faq import worker as worker_module

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
async def test_faq_worker_high_confidence_single_hit_returns_seeded_answer_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from banking.faq import worker as worker_module

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    class FakeRetriever:
        def __init__(self, db, embedding_service=None):
            del db, embedding_service

        async def search(self, query, category=None, limit=5):
            assert query == "transfer fees"
            assert category == "transfers"
            assert limit == 5
            return (
                [
                    FAQRetrievalHit(
                        id="faq_transfer_fees",
                        category="transfers",
                        question="Are there any transfer fees?",
                        answer="Fees are shown before you confirm when they are available.",
                        score=9.0,
                        match_type="keyword",
                    )
                ],
                0.86,
            )

    class FailingLLM:
        async def ainvoke(self, messages):
            del messages
            raise AssertionError("LLM should not be called for a single high-confidence FAQ hit")

    monkeypatch.setattr(worker_module, "EmbeddingService", lambda: object())
    monkeypatch.setattr(worker_module, "capability_block_message", lambda **kwargs: None)
    monkeypatch.setattr(
        "banking.faq.nodes.retrieve.HybridRetriever",
        FakeRetriever,
    )

    faq_worker = worker_module.FAQWorker(llm=FailingLLM(), get_db=lambda: FakeSession())
    result = await faq_worker.run(
        payload={},
        context={"phone_number": "2348000000000", "language": "en"},
        user_message="What are transfer fees?",
    )

    assert result.outcome == FAQOutcome.OK
    assert result.response == "Fees are shown before you confirm when they are available."


@pytest.mark.asyncio
async def test_faq_worker_multi_hit_uses_llm_synthesis(monkeypatch: pytest.MonkeyPatch) -> None:
    from banking.faq import worker as worker_module

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    class FakeRetriever:
        def __init__(self, db, embedding_service=None):
            del db, embedding_service

        async def search(self, query, category=None, limit=5):
            del query, category, limit
            return (
                [
                    FAQRetrievalHit(
                        id="faq_receipt",
                        category="receipts",
                        question="How do I get a receipt?",
                        answer="Ask for a receipt for an eligible successful transfer.",
                        score=9.0,
                        match_type="keyword",
                    ),
                    FAQRetrievalHit(
                        id="faq_receipt_past",
                        category="receipts",
                        question="Can I get receipts for past transfers?",
                        answer="Past eligible successful transfers can have receipts.",
                        score=7.0,
                        match_type="keyword",
                    ),
                ],
                0.82,
            )

    class FakeLLM:
        def __init__(self):
            self.calls = []

        async def ainvoke(self, messages):
            self.calls.append(messages)
            return SimpleNamespace(content="You can request receipts for eligible successful transfers.")

    llm = FakeLLM()
    monkeypatch.setattr(worker_module, "EmbeddingService", lambda: object())
    monkeypatch.setattr(worker_module, "capability_block_message", lambda **kwargs: None)
    monkeypatch.setattr(
        "banking.faq.nodes.retrieve.HybridRetriever",
        FakeRetriever,
    )

    faq_worker = worker_module.FAQWorker(llm=llm, get_db=lambda: FakeSession())
    result = await faq_worker.run(
        payload={},
        context={"phone_number": "2348000000000", "language": "en"},
        user_message="How do receipts work?",
    )

    assert result.outcome == FAQOutcome.OK
    assert result.response == "You can request receipts for eligible successful transfers."
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_faq_worker_disabled_blocks_before_db_or_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    from banking.faq import worker as worker_module

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
