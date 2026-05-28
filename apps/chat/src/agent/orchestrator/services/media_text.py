"""Text normalization for media-backed orchestrator turns."""

import re
from typing import Any

_CAPTION_LABEL_NARRATION_RE = re.compile(
    r"\b(?:narration|memo|note|description|reason|purpose)"
    r"(?:\s+(?:should\s+be|is|as|to\s+be|to|for))?[:\s]+(?P<narration>[^.\n;]+)",
    re.IGNORECASE,
)
_CAPTION_FOR_NARRATION_RE = re.compile(r"\bfor\s+(?P<narration>[^.\n;]+)\s*$", re.IGNORECASE)
_CAPTION_TRANSFER_AMOUNT_RE = re.compile(
    r"\b(?:send|transfer|pay|remit)\b.*?(?:₦|ngn)?\s*"
    r"(?P<amount>\d[\d,]*(?:\.\d+)?)\s*(?P<suffix>[kKhH]?)\b",
    re.IGNORECASE,
)


def combine_media_text(primary_text: str, media_text: str) -> str:
    """Combine user-authored text and interpreted media text for downstream text-only routing."""
    primary = (primary_text or "").strip()
    media = (media_text or "").strip()
    if primary and media:
        return f"{primary}\n\n{media}"
    return primary or media


def _clean_caption_narration_candidate(value: str) -> str | None:
    candidate = re.sub(r"\s+", " ", value).strip(" \t\r\n\"'`.,;:")
    if not candidate or len(candidate) > 80:
        return None
    if re.fullmatch(r"(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKhH]?", candidate, flags=re.IGNORECASE):
        return None
    if re.fullmatch(r"(?:\d[\s,.\-]?){10,11}", candidate):
        return None
    if re.search(r"\b(?:send|transfer|pay|remit|account|acct)\b", candidate, flags=re.IGNORECASE):
        return None
    return candidate


def _caption_narration_hint(caption: str, image_entities: dict[str, Any] | None = None) -> str | None:
    text = caption.strip()
    if not text:
        return None

    match = _CAPTION_LABEL_NARRATION_RE.search(text) or _CAPTION_FOR_NARRATION_RE.search(text)
    if not match:
        return None

    candidate = _clean_caption_narration_candidate(match.group("narration"))
    if not candidate:
        return None

    normalized_candidate = re.sub(r"[^a-z0-9]+", "", candidate.lower())
    entities = image_entities or {}
    for field in ("recipient_name", "recipient_account", "bank_name"):
        entity_value = entities.get(field)
        if not entity_value:
            continue
        normalized_entity = re.sub(r"[^a-z0-9]+", "", str(entity_value).lower())
        if normalized_entity and (
            normalized_candidate == normalized_entity or normalized_candidate in normalized_entity
        ):
            return None

    return candidate


def _caption_amount_hint(caption: str) -> float | None:
    match = _CAPTION_TRANSFER_AMOUNT_RE.search(caption.strip())
    if not match:
        return None
    try:
        amount = float(match.group("amount").replace(",", ""))
    except ValueError:
        return None
    suffix = (match.group("suffix") or "").lower()
    if suffix == "k":
        amount *= 1000.0
    elif suffix == "h":
        amount *= 100.0
    return amount if amount > 0 else None


def format_media_caption_text(caption: str, image_entities: dict[str, Any] | None = None) -> str:
    """Make media captions explicit so downstream text extraction treats them as instructions."""
    text = caption.strip()
    if not text:
        return ""
    lines = [f"User caption/instruction: {text}"]
    amount = _caption_amount_hint(text)
    if amount is not None:
        lines.append(f"Caption-derived transfer fields: amount={amount}.")
    narration = _caption_narration_hint(text, image_entities)
    if narration:
        lines.append(f"Caption-derived transfer fields: narration={narration}.")
    return "\n".join(lines)
