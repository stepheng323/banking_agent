"""WhatsApp Graph API message payload builders."""

from dataclasses import dataclass
from typing import Any


def build_text_message_payload(*, to: str, text: str, preview_url: bool = False) -> dict[str, Any]:
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text, "preview_url": preview_url},
    }


def build_typing_indicator_payload(*, message_id: str) -> dict[str, Any]:
    return {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
        "typing_indicator": {"type": "text"},
    }


def build_button_message_payload(
    *,
    to: str,
    body_text: str,
    buttons: list[dict[str, str]],
    header: str = "",
    footer: str = "",
) -> dict[str, Any]:
    button_rows = [{"type": "reply", "reply": {"id": btn["id"], "title": btn["title"][:20]}} for btn in buttons[:3]]
    interactive_payload: dict[str, Any] = {
        "type": "button",
        "body": {"text": body_text},
        "action": {"buttons": button_rows},
    }
    _maybe_add_header_footer(interactive_payload, header=header, footer=footer)
    return _interactive_message_payload(to=to, interactive_payload=interactive_payload)


def build_list_message_payload(
    *,
    to: str,
    body_text: str,
    options: list[dict[str, str]],
    header: str = "",
    footer: str = "",
    list_button_text: str = "View options",
) -> dict[str, Any]:
    if not options:
        raise ValueError("List options cannot be empty")

    rows: list[dict[str, str]] = []
    for idx, option in enumerate(options[:10], start=1):
        raw_id = str(option.get("id", "")).strip() or str(idx)
        raw_title = str(option.get("title", f"Option {idx}")).strip() or f"Option {idx}"
        row: dict[str, str] = {"id": raw_id, "title": raw_title[:24]}
        description = str(option.get("description", "")).strip()
        if description:
            row["description"] = description[:72]
        rows.append(row)

    interactive_payload: dict[str, Any] = {
        "type": "list",
        "body": {"text": body_text},
        "action": {
            "button": list_button_text[:20],
            "sections": [{"title": "Options", "rows": rows}],
        },
    }
    _maybe_add_header_footer(interactive_payload, header=header, footer=footer)
    return _interactive_message_payload(to=to, interactive_payload=interactive_payload)


@dataclass(frozen=True, slots=True)
class WhatsAppFlowMessagePayload:
    payload: dict[str, Any]
    parameters: dict[str, Any]
    flow_token: str
    flow_action: str
    screen_name: str


def build_flow_message_payload(
    *,
    to: str,
    flow_id: str,
    flow_config: dict[str, Any],
) -> WhatsAppFlowMessagePayload:
    header = flow_config.get("header", "")
    text_body = flow_config.get("text_body", "")
    flow_cta = flow_config.get("flow_cta", "Start")
    screen_name = flow_config.get("screen_name", "")
    footer = flow_config.get("footer", "")
    flow_token = flow_config.get("flow_token", "")
    flow_action = flow_config.get("flow_action", "navigate")
    flow_action_payload = flow_config.get("flow_action_payload")
    parameters: dict[str, Any] = {
        "flow_message_version": "3",
        "flow_token": flow_token or "",
        "flow_id": flow_id,
        "flow_cta": flow_cta,
        "flow_action": flow_action,
    }
    if flow_action_payload is not None:
        parameters["flow_action_payload"] = flow_action_payload
    elif flow_action == "navigate":
        parameters["flow_action_payload"] = {"screen": screen_name}

    interactive_payload: dict[str, Any] = {
        "type": "flow",
        "header": {"type": "text", "text": header},
        "body": {"text": text_body},
        "action": {
            "name": "flow",
            "parameters": parameters,
        },
    }
    if footer and footer.strip():
        interactive_payload["footer"] = {"text": footer}

    return WhatsAppFlowMessagePayload(
        payload=_interactive_message_payload(to=to, interactive_payload=interactive_payload),
        parameters=parameters,
        flow_token=flow_token,
        flow_action=flow_action,
        screen_name=screen_name,
    )


def build_image_message_payload(*, to: str, media_id: str, caption: str = "") -> dict[str, Any]:
    image_payload: dict[str, Any] = {"id": media_id}
    if caption:
        image_payload["caption"] = caption
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "image",
        "image": image_payload,
    }


def build_document_message_payload(
    *,
    to: str,
    media_id: str,
    filename: str,
    caption: str = "",
) -> dict[str, Any]:
    document_payload: dict[str, Any] = {
        "id": media_id,
        "filename": filename,
    }
    if caption:
        document_payload["caption"] = caption
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "document",
        "document": document_payload,
    }


def _interactive_message_payload(*, to: str, interactive_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": interactive_payload,
    }


def _maybe_add_header_footer(
    interactive_payload: dict[str, Any],
    *,
    header: str,
    footer: str,
) -> None:
    if header and header.strip():
        interactive_payload["header"] = {"type": "text", "text": header}
    if footer and footer.strip():
        interactive_payload["footer"] = {"text": footer}
