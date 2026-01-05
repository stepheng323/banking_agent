"""Seed FAQ entries from markdown files.

This script parses markdown files in data/faq/ and populates the faq_entries table.

Usage:
    python -m scripts.seed_faq
"""

import re
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from shared.database.connection import get_db
from shared.database.models import FAQEntry
from shared.repositories.faq_repository import FAQRepository
from shared.utils.logging import get_logger

logger = get_logger(__name__)

FAQ_DIR = project_root / "data" / "faq"


def extract_keywords(question: str, answer: str) -> list[str]:
    """Extract keywords from question and answer text.

    Simple extraction: words longer than 3 chars, excluding common words.
    """
    stopwords = {
        "what",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "whose",
        "why",
        "how",
        "this",
        "that",
        "these",
        "those",
        "there",
        "here",
        "have",
        "has",
        "had",
        "will",
        "would",
        "could",
        "should",
        "might",
        "just",
        "also",
        "only",
        "about",
        "after",
        "before",
        "between",
        "into",
        "through",
        "during",
        "with",
        "from",
        "your",
        "you",
        "are",
        "the",
        "and",
        "for",
        "can",
        "does",
        "don",
        "did",
        "was",
        "were",
    }

    # Combine question and first sentence of answer
    text = question.lower() + " " + answer.split(".")[0].lower()

    # Extract words (alphanumeric only)
    words = re.findall(r"\b[a-z]+\b", text)

    # Filter: length > 3, not a stopword, unique
    keywords = list({w for w in words if len(w) > 3 and w not in stopwords})

    return keywords[:10]  # Max 10 keywords


def extract_tags(category: str, question: str) -> list[str]:
    """Extract tags based on category and common patterns."""
    tags = [category]

    # Add tags based on question content
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
        if any(p in question_lower for p in patterns):
            tags.append(tag)

    return list(set(tags))


def parse_markdown_file(filepath: Path) -> list[dict]:
    """Parse a markdown FAQ file into entries.

    Expected format:
    # Category: CategoryName

    ## Question text
    Answer text (can be multiple paragraphs)

    ## Another question
    Another answer
    """
    entries = []
    content = filepath.read_text(encoding="utf-8")

    # Extract category from header
    category_match = re.search(r"^# Category:\s*(.+)$", content, re.MULTILINE)
    if not category_match:
        logger.warning(f"No category found in {filepath}")
        return []

    category = category_match.group(1).strip().lower().replace(" ", "_")

    # Split by ## headers (questions)
    sections = re.split(r"^## ", content, flags=re.MULTILINE)[1:]  # Skip category header

    for section in sections:
        lines = section.strip().split("\n")
        if not lines:
            continue

        question = lines[0].strip()
        answer = "\n".join(lines[1:]).strip()

        if not question or not answer:
            continue

        entry = {
            "category": category,
            "question": question,
            "answer": answer,
            "keywords": extract_keywords(question, answer),
            "tags": extract_tags(category, question),
            "priority": 0,
            "is_active": True,
        }
        entries.append(entry)

    return entries


def seed_faq():
    """Parse all FAQ markdown files and seed the database."""
    if not FAQ_DIR.exists():
        logger.error(f"FAQ directory not found: {FAQ_DIR}")
        return

    all_entries = []

    for md_file in FAQ_DIR.glob("*.md"):
        logger.info(f"Parsing {md_file.name}...")
        entries = parse_markdown_file(md_file)
        all_entries.extend(entries)
        logger.info(f"  Found {len(entries)} entries")

    if not all_entries:
        logger.warning("No FAQ entries found")
        return

    logger.info(f"Total entries: {len(all_entries)}")

    # Generate embeddings for all entries
    logger.info("Generating embeddings...")
    try:
        from apps.core.src.agent.sub_agents.faq.retrieval.embeddings import EmbeddingService

        embedding_service = EmbeddingService()

        # Prepare texts for embedding (combine question + answer for richer context)
        texts = [f"{e['question']}\n{e['answer']}" for e in all_entries]

        # Batch generate embeddings
        embeddings = embedding_service.get_embeddings_sync(texts)

        # Add embeddings to entries
        for entry, embedding in zip(all_entries, embeddings, strict=True):
            entry["embedding"] = embedding

        logger.info(f"Generated {len(embeddings)} embeddings")

    except Exception as e:
        logger.warning(f"Failed to generate embeddings: {e}")
        logger.info("Continuing without embeddings - keyword search will still work")

    # Insert into database
    with next(get_db()) as db:
        repo = FAQRepository(db)

        # Clear existing entries (optional - comment out to append)
        existing = db.query(FAQEntry).delete()
        logger.info(f"Cleared {existing} existing entries")

        # Bulk create
        created = repo.bulk_create(all_entries)
        db.commit()

        logger.info(f"Created {len(created)} FAQ entries")

        # Print summary by category
        categories = {}
        for entry in created:
            categories[entry.category] = categories.get(entry.category, 0) + 1

        for cat, count in sorted(categories.items()):
            logger.info(f"  {cat}: {count} entries")


if __name__ == "__main__":
    seed_faq()
