"""Safe grounding context for bounded conversational replies."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from shared.branding import brand_template_params

_WHITESPACE_RE = re.compile(r"\s+")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_URL_OR_HANDLE_RE = re.compile(r"(?:https?://|www\.|@)")
_SENSITIVE_TURN_RE = re.compile(r"\b(?:pin|otp|password|passcode|authorization|auth\s*code|secret)\b", re.IGNORECASE)
_LONG_NUMBER_RE = re.compile(r"\b\d{8,16}\b")

_VALID_TOPICS = {
    "brand_origin",
    "product_identity",
    "balance_result",
    "transfer_result",
    "data_result",
    "airtime_result",
    "unsupported_boundary",
    "support",
    "casual",
}
_RESPONSE_KEY_TOPICS = {
    "conversational.brand_origin": "brand_origin",
    "conversational.identity": "product_identity",
    "conversational.greeting": "casual",
    "conversational.greeting_named": "casual",
    "conversational.checkin": "casual",
    "conversational.appreciation": "casual",
    "conversational.casual_chat": "casual",
    "conversational.out_of_scope": "unsupported_boundary",
    "capability.unsupported_unavailable": "unsupported_boundary",
    "capability.unsupported_unavailable_followup": "unsupported_boundary",
    "capability.unsupported_unavailable_firm": "unsupported_boundary",
}


def _fold_text(text: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    without_marks = "".join(char for char in normalized if not unicodedata.combining(char))
    return _WHITESPACE_RE.sub(" ", without_marks.casefold()).strip()


def _clean_text(text: str | None, *, max_chars: int = 320) -> str | None:
    raw = _CONTROL_RE.sub(" ", str(text or ""))
    cleaned = _WHITESPACE_RE.sub(" ", raw).strip()
    if not cleaned or _SENSITIVE_TURN_RE.search(cleaned):
        return None
    masked = _LONG_NUMBER_RE.sub(lambda match: f"...{match.group(0)[-4:]}", cleaned)
    return masked[:max_chars].strip() or None


def safe_display_name(raw_name: Any) -> str | None:
    """Return a short display name safe for conversational tone only."""
    if not isinstance(raw_name, str):
        return None
    cleaned = _CONTROL_RE.sub(" ", raw_name)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip(" ,.;:!?\t\r\n")
    if not cleaned or _URL_OR_HANDLE_RE.search(cleaned) or any(char.isdigit() for char in cleaned):
        return None
    first_token = cleaned.split()[0]
    first_token = first_token.strip(" ,.;:!?'\"")
    if not first_token or len(first_token) > 40 or not first_token[0].isalpha():
        return None
    if not all(char.isalpha() or char in {"'", "-", "."} for char in first_token):
        return None
    return first_token


def conversation_display_name(loaded_context: dict[str, Any] | None) -> str | None:
    if not isinstance(loaded_context, dict):
        return None
    profile = loaded_context.get("profile")
    if isinstance(profile, dict):
        for key in ("first_name", "full_name", "name"):
            name = safe_display_name(profile.get(key))
            if name:
                return name

    channel_metadata = loaded_context.get("channel_metadata")
    if isinstance(channel_metadata, dict):
        for key in ("sender_display_name", "profile_name", "from_first_name"):
            name = safe_display_name(channel_metadata.get(key))
            if name:
                return name
    return None


def _clean_topic(raw_topic: str | None) -> str | None:
    topic = str(raw_topic or "").strip().lower()
    return topic if topic in _VALID_TOPICS else None


def _turn_topic(turn: dict[str, Any]) -> str | None:
    topic = _clean_topic(turn.get("topic") or turn.get("conversation_topic"))
    if topic:
        return topic
    metadata = turn.get("metadata")
    if isinstance(metadata, dict):
        return _clean_topic(metadata.get("topic") or metadata.get("conversation_topic"))
    return None


def _last_assistant_turn(history: list[Any]) -> tuple[str | None, str | None]:
    for turn in reversed(history):
        if not isinstance(turn, dict):
            continue
        if str(turn.get("role") or "").strip().lower() != "assistant":
            continue
        content = _clean_text(turn.get("content"))
        if content:
            return content, _turn_topic(turn)
    return None, None


def _recent_turns(history: list[Any], *, limit: int = 6) -> list[dict[str, str]]:
    turns: list[dict[str, str]] = []
    for turn in history[-limit:]:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = _clean_text(turn.get("content"), max_chars=220)
        if content:
            turns.append({"role": role, "content": content})
    return turns


def infer_last_topic(last_assistant_message: str | None) -> str | None:
    folded = _fold_text(last_assistant_message)
    if not folded:
        return None
    brand_params = brand_template_params()
    app_name = _fold_text(str(brand_params.get("app_name") or ""))
    app_short = _fold_text(str(brand_params.get("app_name_short") or ""))

    if "ring of water" in folded or "liquidity and flow" in folded:
        return "brand_origin"
    if (app_name and app_name in folded) or (app_short and app_short in folded):
        if "banking assistant" in folded or "transfers" in folded or "airtime/data" in folded:
            return "product_identity"
    if "balance" in folded or "available balance" in folded:
        return "balance_result"
    if "transfer" in folded or "sent to" in folded or "has been sent" in folded:
        return "transfer_result"
    if "data" in folded and ("purchase" in folded or "bundle" in folded or "plan" in folded):
        return "data_result"
    if "airtime" in folded:
        return "airtime_result"
    if "can't help with" in folded or "cannot help with" in folded or "unsupported" in folded:
        return "unsupported_boundary"
    return "casual"


def conversation_topic_for_response(
    response_text: str | None,
    *,
    response_key: str | None = None,
    semantic_path_shape: str | None = None,
    routing_decision: str | None = None,
) -> str | None:
    """Infer a safe topic label to persist with assistant history.

    Prefer structured route metadata; only fall back to response text when no
    explicit route signal exists. The topic is for conversational grounding, not
    for transaction authorization or routing.
    """
    if response_key:
        topic = _RESPONSE_KEY_TOPICS.get(response_key)
        if topic:
            return topic

    path = (semantic_path_shape or "").strip().lower()
    decision = (routing_decision or "").strip().lower()
    joined = f"{path} {decision}"
    if any(token in joined for token in ("brand_origin", "contextual_meta_followup")):
        inferred = infer_last_topic(response_text)
        return inferred if inferred in {"brand_origin", "product_identity"} else "casual"
    if "unsupported" in joined or "capability_boundary" in joined or "out_of_scope" in joined:
        return "unsupported_boundary"
    if "balance" in joined or "account_domain" in joined:
        return "balance_result"
    if "transfer" in joined:
        return "transfer_result"
    if "data" in joined:
        return "data_result"
    if "airtime" in joined:
        return "airtime_result"
    if "support" in joined or "receipt" in joined:
        return "support"

    return infer_last_topic(response_text)


def build_conversation_grounding(loaded_context: dict[str, Any] | None) -> dict[str, Any]:
    context = loaded_context if isinstance(loaded_context, dict) else {}
    history = context.get("history")
    history_list = history if isinstance(history, list) else []
    last_assistant, explicit_topic = _last_assistant_turn(history_list)
    return {
        "display_name": conversation_display_name(context),
        "recent_turns": _recent_turns(history_list),
        "last_assistant_message": last_assistant,
        "last_topic": explicit_topic or infer_last_topic(last_assistant),
    }


def attach_conversation_grounding(loaded_context: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(loaded_context)
    enriched["conversation_grounding"] = build_conversation_grounding(enriched)
    return enriched
