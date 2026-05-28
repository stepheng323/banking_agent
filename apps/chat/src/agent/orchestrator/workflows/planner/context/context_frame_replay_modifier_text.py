"""Text cleanup shared by context-frame replay modifier parsers."""

import re


def _clean_replay_modifier_text(value: str | None) -> str | None:
    if not value:
        return None

    cleaned = re.split(
        r"\b(?:from|using|use|debit|charge|switch(?:\s+it)?\s+to|"
        r"change\s+source(?:\s+account)?\s+to|with\s+(?:₦|ngn|naira|\d))\b",
        value,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    cleaned = re.split(
        r"\b(?:lati(?:\s+inu)?|lo|daga|(?:yi\s+)?amfani\s+da|ta\s+hanyar|site\s+na|jiri)\b",
        cleaned,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    cleaned = re.split(r"\b(?:instead|please|pls)\b", cleaned, maxsplit=1, flags=re.IGNORECASE)[0]
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" \"'.,;:!?")
    cleaned = re.sub(r"^(?:as|to|is|be)\s+", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned or None


__all__ = ["_clean_replay_modifier_text"]
