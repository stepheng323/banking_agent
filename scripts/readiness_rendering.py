"""Rendering helpers for readiness transcript assertions."""

from __future__ import annotations

from typing import Any


def stringify_payload(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _intent_to_dict(intent: Any) -> dict[str, Any]:
    if hasattr(intent, "to_dict"):
        data = intent.to_dict()
        if isinstance(data, dict):
            return data
    if isinstance(intent, dict):
        return intent
    return {"type": type(intent).__name__, "value": str(intent)}


def render_orchestrator_result(result: dict[str, Any]) -> str:
    outbox = result.get("outbox") or []
    rendered: list[str] = []
    for entry in outbox:
        if not isinstance(entry, dict):
            continue
        entry_type = entry.get("type")
        if entry_type == "say":
            rendered.append(stringify_payload(entry.get("text")))
        elif entry_type == "request_confirmation":
            header = stringify_payload(entry.get("header"))
            summary = stringify_payload(entry.get("summary"))
            rendered.append("\n".join(part for part in (header, summary) if part))
        elif entry_type == "auth_request":
            header = stringify_payload(entry.get("header"))
            summary = stringify_payload(entry.get("summary"))
            rendered.append("\n".join(part for part in (header, summary) if part) or "PIN authorization requested.")
        elif entry_type == "show_options":
            title = stringify_payload(entry.get("title"))
            options = entry.get("options") or []
            option_lines = []
            for idx, option in enumerate(options, start=1):
                if isinstance(option, dict):
                    label = option.get("label") or option.get("title") or option.get("text") or option
                    option_lines.append(f"{idx}. {label}")
                else:
                    option_lines.append(f"{idx}. {option}")
            rendered.append("\n".join([title, *option_lines]).strip())
        elif entry_type == "show_receipt":
            receipt = entry.get("receipt") or {}
            caption = stringify_payload(entry.get("caption"))
            rendered.append("\n".join(part for part in (caption, str(receipt)) if part))
        else:
            rendered.append(str(entry))

    if rendered:
        return "\n\n".join(part for part in rendered if part).strip()

    intents = result.get("intents") or []
    for intent in intents:
        data = _intent_to_dict(intent)
        rendered.append(
            "\n".join(
                part
                for part in (
                    stringify_payload(data.get("header")),
                    stringify_payload(data.get("summary")),
                    stringify_payload(data.get("text")),
                    stringify_payload(data.get("fallback_text")),
                )
                if part
            )
        )
    if rendered:
        return "\n\n".join(part for part in rendered if part).strip()

    return stringify_payload(result.get("text")).strip()


def duplicate_visible_blocks(response: str) -> tuple[str, ...]:
    blocks = tuple(part.strip() for part in response.split("\n\n") if part.strip())
    seen: set[str] = set()
    duplicates: list[str] = []
    for block in blocks:
        if block in seen and block not in duplicates:
            duplicates.append(block)
        seen.add(block)
    return tuple(duplicates)
