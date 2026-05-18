from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace

import pytest

import scripts.seed_faq as seed_faq


def _write_faq(path: Path, content: str) -> Path:
    path.write_text(dedent(content).strip() + "\n", encoding="utf-8")
    return path


def test_parse_current_faq_docs() -> None:
    entries = seed_faq.parse_faq_directory(Path("data/faq"))

    assert len(entries) == 53
    assert {entry["category"] for entry in entries} == {
        "account",
        "data_purchase",
        "general",
        "receipts",
        "security",
        "transfers",
    }
    assert all(entry["question"] and entry["answer"] for entry in entries)
    assert all(entry["keywords"] for entry in entries)
    assert all(entry["tags"] for entry in entries)


def test_category_names_normalize(tmp_path: Path) -> None:
    entries = seed_faq.parse_markdown_file(
        _write_faq(
            tmp_path / "category.md",
            """
            # Category: Data Purchase

            ## How do I buy data?
            Ask Narya AI to buy data and review the plan before confirming.
            """,
        )
    )

    assert entries[0]["category"] == "data_purchase"


def test_missing_category_fails(tmp_path: Path) -> None:
    faq_file = _write_faq(
        tmp_path / "bad.md",
        """
        ## How do I buy data?
        Ask Narya AI to buy data.
        """,
    )

    with pytest.raises(seed_faq.FAQParseError, match="missing '# Category:'"):
        seed_faq.parse_markdown_file(faq_file)


def test_empty_answer_fails(tmp_path: Path) -> None:
    faq_file = _write_faq(
        tmp_path / "bad.md",
        """
        # Category: General

        ## What is Narya AI?
        """,
    )

    with pytest.raises(seed_faq.FAQParseError, match="missing an answer"):
        seed_faq.parse_markdown_file(faq_file)


def test_generated_keywords_and_tags_are_deterministic() -> None:
    question = "How do I transfer money?"
    answer = "Transfer money to linked accounts. Review details before confirming."

    assert seed_faq.extract_keywords(question, answer) == [
        "transfer",
        "money",
        "linked",
        "accounts",
    ]
    assert seed_faq.extract_keywords(question, answer) == seed_faq.extract_keywords(question, answer)
    assert seed_faq.extract_tags("transfers", question) == ["transfers", "transfer"]


@pytest.mark.asyncio
async def test_seed_dry_run_parses_without_embeddings_or_db_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_faq(
        tmp_path / "general.md",
        """
        # Category: General

        ## What is Narya AI?
        Narya AI is a chat-based banking assistant.
        """,
    )
    monkeypatch.setattr(seed_faq, "add_embeddings", lambda entries: pytest.fail("embeddings called"))

    async def fail_replace(entries):
        pytest.fail("DB write called")

    monkeypatch.setattr(seed_faq, "replace_all_faq_entries", fail_replace)

    assert await seed_faq.seed_faq(faq_dir=tmp_path, dry_run=True) == 0


@pytest.mark.asyncio
async def test_seed_no_embeddings_replaces_entries_without_calling_openai(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_faq(
        tmp_path / "general.md",
        """
        # Category: General

        ## What is Narya AI?
        Narya AI is a chat-based banking assistant.
        """,
    )
    captured_entries = []
    monkeypatch.setattr(seed_faq, "add_embeddings", lambda entries: pytest.fail("embeddings called"))

    async def fake_replace(entries):
        captured_entries.extend(entries)
        return entries

    monkeypatch.setattr(seed_faq, "replace_all_faq_entries", fake_replace)

    assert await seed_faq.seed_faq(faq_dir=tmp_path, no_embeddings=True) == 0
    assert len(captured_entries) == 1
    assert "embedding" not in captured_entries[0]


def test_embedding_failure_keeps_keyword_searchable_entries() -> None:
    entries = [
        {
            "category": "general",
            "question": "What is Narya AI?",
            "answer": "Narya AI is a chat-based banking assistant.",
            "keywords": ["narya"],
            "tags": ["general"],
            "priority": 0,
            "is_active": True,
        }
    ]

    class FailingEmbeddingService:
        def get_embeddings_sync(self, texts):
            raise RuntimeError("provider unavailable")

    assert not seed_faq.add_embeddings(entries, service_factory=FailingEmbeddingService)
    assert "embedding" not in entries[0]
    assert entries[0]["keywords"] == ["narya"]


@pytest.mark.asyncio
async def test_replace_all_deletes_and_inserts_in_one_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    class FakeTransaction:
        async def __aenter__(self):
            events.append("begin")

        async def __aexit__(self, exc_type, exc, traceback):
            events.append("commit")

    class FakeSession:
        async def __aenter__(self):
            events.append("session")
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            events.append("close")

        def begin(self):
            return FakeTransaction()

        async def execute(self, stmt):
            del stmt
            events.append("delete")
            return SimpleNamespace(rowcount=3)

    class FakeRepository:
        def __init__(self, db):
            assert isinstance(db, FakeSession)

        async def bulk_create(self, entries):
            events.append("insert")
            return entries

    monkeypatch.setattr(seed_faq, "get_db_session", lambda: FakeSession())
    monkeypatch.setattr(seed_faq, "FAQRepository", FakeRepository)

    entries = [
        {
            "category": "general",
            "question": "What is Narya AI?",
            "answer": "Narya AI is a chat-based banking assistant.",
            "keywords": ["narya"],
            "tags": ["general"],
            "priority": 0,
            "is_active": True,
        }
    ]

    assert await seed_faq.replace_all_faq_entries(entries) == entries
    assert events == ["session", "begin", "delete", "insert", "commit", "close"]
