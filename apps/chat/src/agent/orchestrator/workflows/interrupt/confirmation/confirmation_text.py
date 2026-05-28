import re


def _normalize_recipient_match_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _digits_only(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\D+", "", value)


__all__ = [
    "_digits_only",
    "_normalize_recipient_match_text",
]
