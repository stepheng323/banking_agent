"""Query normalization for FAQ retrieval."""

import re

from shared.utils.logging import get_logger

logger = get_logger(__name__)


class QueryNormalizer:
    """Normalize user queries for FAQ matching."""

    # Filler words to remove
    FILLER_WORDS = {
        "please",
        "can",
        "could",
        "would",
        "should",
        "might",
        "want",
        "need",
        "like",
        "help",
        "tell",
        "explain",
        "what",
        "how",
        "when",
        "where",
        "why",
        "who",
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "do",
        "does",
        "did",
        "have",
        "has",
        "had",
        "i",
        "me",
        "my",
        "we",
        "our",
        "you",
        "your",
        "just",
        "really",
        "very",
        "actually",
        "basically",
        "about",
        "for",
        "with",
        "this",
        "that",
    }

    # Synonym mappings for common variations
    SYNONYMS = {
        "money": ["cash", "funds", "payment"],
        "transfer": ["send", "pay", "wire", "remit"],
        "account": ["acct", "acc"],
        "password": ["passcode", "secret"],
        "pin": ["code", "passcode"],
        "fee": ["charge", "cost", "price"],
        "limit": ["maximum", "max", "cap"],
        "receipt": ["proof", "confirmation", "slip"],
        "data": ["bundle", "mb", "gb", "airtime"],
        "bank": ["financial", "institution"],
    }

    def normalize(self, query: str) -> str:
        """Normalize a query string.

        - Lowercase
        - Remove punctuation
        - Remove filler words
        - Collapse synonyms to canonical form
        """
        # Lowercase
        query = query.lower()

        # Remove punctuation except spaces
        query = re.sub(r"[^\w\s]", " ", query)

        # Split into words
        words = query.split()

        # Remove filler words
        words = [w for w in words if w not in self.FILLER_WORDS]

        # Collapse synonyms
        normalized_words = []
        for word in words:
            canonical = self._find_canonical(word)
            normalized_words.append(canonical)

        # Rejoin
        normalized = " ".join(normalized_words)

        # Remove extra whitespace
        normalized = re.sub(r"\s+", " ", normalized).strip()

        return normalized

    def _find_canonical(self, word: str) -> str:
        """Find the canonical form of a word if it's a synonym."""
        for canonical, synonyms in self.SYNONYMS.items():
            if word in synonyms or word == canonical:
                return canonical
        return word

    def extract_keywords(self, query: str) -> list[str]:
        """Extract keywords from a normalized query.

        Returns unique words sorted by potential relevance (longer words first).
        """
        normalized = self.normalize(query)
        words = normalized.split()

        # Remove very short words
        keywords = [w for w in words if len(w) > 2]

        # Remove duplicates while preserving order
        seen = set()
        unique_keywords = []
        for kw in keywords:
            if kw not in seen:
                seen.add(kw)
                unique_keywords.append(kw)

        # Sort by length (longer = more specific)
        unique_keywords.sort(key=len, reverse=True)

        return unique_keywords[:8]  # Max 8 keywords
