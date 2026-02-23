"""Input sanitization utilities for fault tolerance.

This module provides functions to sanitize user input before processing,
protecting against:
- Memory exhaustion (message length limits)
- Control character injection
- Unicode normalization issues
- Prompt injection via special characters
"""

import re
import unicodedata

# Maximum message length (characters) - protects against huge payloads
MAX_MESSAGE_LENGTH = 500

# Maximum length for extracted fields (account numbers, names, etc.)
MAX_FIELD_LENGTH = 100


def sanitize_message(text: str | None, max_length: int = MAX_MESSAGE_LENGTH) -> str:
    """
    Sanitize user message input.

    This function should be called on ALL incoming user messages before
    any processing occurs.

    Args:
        text: Raw user input text
        max_length: Maximum allowed length (default 500 chars)

    Returns:
        Sanitized, safe string
    """
    if not text:
        return ""

    text = text[:max_length]

    # 2. Unicode normalization (NFKC decomposes then recomposes)
    # This handles zero-width chars, homoglyphs, etc.
    text = unicodedata.normalize("NFKC", text)

    # 3. Remove null bytes (can cause issues in C-backed libraries)
    text = text.replace("\x00", "")

    # 4. Strip control characters except common whitespace
    # Keep: newline, carriage return, tab
    # Remove: other control chars (0x00-0x1F except 0x09, 0x0A, 0x0D)
    text = "".join(c for c in text if c.isprintable() or c in "\n\r\t")

    # 5. Collapse excessive whitespace (but preserve single newlines for readability)
    text = re.sub(r"[ \t]+", " ", text)  # Collapse spaces/tabs
    text = re.sub(r"\n{3,}", "\n\n", text)  # Max 2 consecutive newlines

    # 6. Strip leading/trailing whitespace
    text = text.strip()

    return text


def sanitize_field(value: str | None, max_length: int = MAX_FIELD_LENGTH) -> str:
    """
    Sanitize a single field value (account number, name, etc.).

    More restrictive than message sanitization - removes all newlines.

    Args:
        value: Raw field value
        max_length: Maximum allowed length (default 200 chars)

    Returns:
        Sanitized string
    """
    if not value:
        return ""

    # Start with message sanitization
    value = sanitize_message(value, max_length)

    # Remove all newlines (fields should be single-line)
    value = value.replace("\n", " ").replace("\r", " ")

    # Collapse multiple spaces
    value = re.sub(r" +", " ", value)

    return value.strip()


def sanitize_account_number(account_number: str | None) -> str:
    """
    Sanitize an account number - only digits allowed.

    Args:
        account_number: Raw account number input

    Returns:
        Digits only, max 20 characters
    """
    if not account_number:
        return ""

    # Extract only digits
    digits = "".join(c for c in str(account_number) if c.isdigit())

    # Limit length (Nigerian accounts are 10 digits, but allow some buffer)
    return digits[:20]


def is_suspicious_input(text: str) -> bool:
    """
    Check if input contains suspicious patterns that might indicate an attack.

    This is a heuristic check - suspicious inputs are still processed,
    but this can be used for logging/monitoring.

    Args:
        text: Sanitized text to check

    Returns:
        True if suspicious patterns detected
    """
    if not text:
        return False

    text_lower = text.lower()

    suspicious_patterns = [
        "ignore previous instructions",
        "ignore all instructions",
        "disregard above",
        "forget everything",
        "new instructions:",
        "system prompt:",
        "you are now",
        "pretend to be",
        "act as if",
        "\\n\\n\\n",  # Excessive newlines (escaped)
        "<script>",
        "javascript:",
        "data:text",
    ]

    return any(pattern in text_lower for pattern in suspicious_patterns)
