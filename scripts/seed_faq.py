"""Seed FAQ entries from markdown files.

This script parses markdown files in data/faq/ and replaces the faq_entries
table contents in one transaction.

Usage:
    python -m scripts.seed_faq
    python -m scripts.seed_faq --dry-run
    python -m scripts.seed_faq --no-embeddings
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import delete

# Allow direct execution as `python scripts/seed_faq.py` from outside repo root.
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from apps.chat.src.agent.workers.faq.retrieval.embeddings import EmbeddingService
from shared.branding import render_brand_template
from shared.database.connection import get_db_session
from shared.database.models import FAQEntry
from banking.knowledge.repositories.faq_repository import FAQRepository
from shared.utils.logging import get_logger

logger = get_logger(__name__)

FAQ_DIR = project_root / "data" / "faq"
FAQEntryPayload = dict[str, Any]


class FAQParseError(ValueError):
    """Raised when FAQ markdown cannot be ingested safely."""


def _normalize_category(raw_category: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", raw_category.strip().lower()).strip("_")


def _unique_in_order(values: list[str], limit: int | None = None) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
        if limit is not None and len(output) >= limit:
            break
    return output


def extract_keywords(question: str, answer: str) -> list[str]:
    """Extract deterministic keywords from question and answer text."""
    stopwords = {
        "about",
        "after",
        "also",
        "and",
        "are",
        "before",
        "between",
        "can",
        "could",
        "did",
        "does",
        "don",
        "during",
        "for",
        "from",
        "had",
        "has",
        "have",
        "here",
        "how",
        "into",
        "just",
        "might",
        "only",
        "should",
        "that",
        "the",
        "there",
        "these",
        "this",
        "those",
        "through",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "whose",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
    }

    first_answer_sentence = answer.split(".")[0].lower()
    text = f"{question.lower()} {first_answer_sentence}"
    words = re.findall(r"\b[a-z]+\b", text)
    return _unique_in_order([w for w in words if len(w) > 3 and w not in stopwords], limit=10)


def extract_tags(category: str, question: str) -> list[str]:
    """Extract deterministic tags from category and question text."""
    tags = [category]
    question_lower = question.lower()
    tag_patterns = {
        "fee": ["fees", "charges", "cost"],
        "time": ["how long", "when", "duration", "time"],
        "limit": ["limit", "maximum", "minimum"],
        "security": ["safe", "secure", "protect", "pin", "password"],
        "error": ["fail", "error", "wrong", "problem", "issue"],
        "cancel": ["cancel", "reverse", "undo"],
        "receipt": ["receipt", "proof", "confirm"],
        "account": ["account", "bank", "link"],
        "data": ["data", "bundle", "mb", "gb"],
        "transfer": ["transfer", "send", "pay"],
    }

    for tag, patterns in tag_patterns.items():
        if any(pattern in question_lower for pattern in patterns):
            tags.append(tag)

    return _unique_in_order(tags)


def parse_markdown_file(filepath: Path) -> list[FAQEntryPayload]:
    """Parse one FAQ markdown file into repository payloads."""
    content = render_brand_template(filepath.read_text(encoding="utf-8"))

    category_match = re.search(r"^# Category:\s*(.+)$", content, re.MULTILINE)
    if not category_match:
        raise FAQParseError(f"{filepath}: missing '# Category:' header")

    category = _normalize_category(category_match.group(1))
    if not category:
        raise FAQParseError(f"{filepath}: empty category header")

    sections = re.split(r"^## ", content, flags=re.MULTILINE)[1:]
    if not sections:
        raise FAQParseError(f"{filepath}: no FAQ sections found")

    entries: list[FAQEntryPayload] = []
    for index, section in enumerate(sections, start=1):
        lines = section.strip().splitlines()
        question = lines[0].strip() if lines else ""
        answer = "\n".join(lines[1:]).strip()

        if not question:
            raise FAQParseError(f"{filepath}: section {index} is missing a question")
        if not answer:
            raise FAQParseError(f"{filepath}: question '{question}' is missing an answer")

        entries.append(
            {
                "category": category,
                "question": question,
                "answer": answer,
                "keywords": extract_keywords(question, answer),
                "tags": extract_tags(category, question),
                "priority": 0,
                "is_active": True,
            }
        )

    return entries


def parse_faq_directory(faq_dir: Path) -> list[FAQEntryPayload]:
    """Parse and validate all markdown files in a FAQ directory."""
    if not faq_dir.exists():
        raise FAQParseError(f"FAQ directory not found: {faq_dir}")
    if not faq_dir.is_dir():
        raise FAQParseError(f"FAQ path is not a directory: {faq_dir}")

    markdown_files = sorted(faq_dir.glob("*.md"))
    if not markdown_files:
        raise FAQParseError(f"No markdown FAQ files found in {faq_dir}")

    entries: list[FAQEntryPayload] = []
    for markdown_file in markdown_files:
        parsed = parse_markdown_file(markdown_file)
        logger.info("Parsed %s: %s entries", markdown_file.name, len(parsed))
        entries.extend(parsed)

    if not entries:
        raise FAQParseError(f"No FAQ entries parsed from {faq_dir}")

    return entries


def add_embeddings(
    entries: list[FAQEntryPayload],
    *,
    service_factory: Callable[[], Any] = EmbeddingService,
) -> bool:
    """Add embeddings in-place. Return False if embedding generation fails."""
    if not entries:
        return True

    try:
        embedding_service = service_factory()
        texts = [f"{entry['question']}\n{entry['answer']}" for entry in entries]
        embeddings = embedding_service.get_embeddings_sync(texts)
        if len(embeddings) != len(entries):
            raise RuntimeError(
                f"Embedding count mismatch: got {len(embeddings)} for {len(entries)} FAQ entries"
            )
        for entry, embedding in zip(entries, embeddings, strict=True):
            entry["embedding"] = embedding
        logger.info("Generated %s FAQ embeddings", len(embeddings))
        return True
    except Exception as exc:  # pragma: no cover - exact provider failures vary
        logger.warning("Failed to generate FAQ embeddings: %s", exc)
        logger.info("Continuing with keyword/fuzzy-only FAQ entries")
        return False


async def replace_all_faq_entries(entries: list[FAQEntryPayload]) -> list[FAQEntry]:
    """Replace all FAQ rows in one transaction."""
    async with get_db_session() as db:
        async with db.begin():
            delete_result = await db.execute(delete(FAQEntry))
            repo = FAQRepository(db)
            created = await repo.bulk_create(entries)

        logger.info("Cleared %s existing FAQ entries", delete_result.rowcount or 0)
        logger.info("Created %s FAQ entries", len(created))
        return created


def _category_counts(entries: list[FAQEntryPayload] | list[FAQEntry]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        category = entry.category if isinstance(entry, FAQEntry) else str(entry["category"])
        counts[category] = counts.get(category, 0) + 1
    return counts


def _log_summary(entries: list[FAQEntryPayload] | list[FAQEntry]) -> None:
    logger.info("FAQ entry summary:")
    for category, count in sorted(_category_counts(entries).items()):
        logger.info("  %s: %s", category, count)


async def seed_faq(
    *,
    faq_dir: Path = FAQ_DIR,
    dry_run: bool = False,
    no_embeddings: bool = False,
) -> int:
    """Parse FAQ markdown and optionally replace the FAQ table."""
    try:
        entries = parse_faq_directory(faq_dir)
    except FAQParseError as exc:
        logger.error("%s", exc)
        return 1

    logger.info("Parsed %s FAQ entries from %s", len(entries), faq_dir)
    _log_summary(entries)

    if dry_run:
        logger.info("Dry run complete; no embeddings generated and no DB writes performed")
        return 0

    if no_embeddings:
        logger.info("Skipping FAQ embedding generation")
    else:
        add_embeddings(entries)

    created = await replace_all_faq_entries(entries)
    _log_summary(created)
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed FAQ entries from markdown files")
    parser.add_argument(
        "--faq-dir",
        type=Path,
        default=FAQ_DIR,
        help="Directory containing FAQ markdown files",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and summarize markdown without embeddings or DB writes",
    )
    parser.add_argument(
        "--no-embeddings",
        action="store_true",
        help="Seed FAQ rows without generating OpenAI embeddings",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    return asyncio.run(
        seed_faq(
            faq_dir=args.faq_dir,
            dry_run=args.dry_run,
            no_embeddings=args.no_embeddings,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
