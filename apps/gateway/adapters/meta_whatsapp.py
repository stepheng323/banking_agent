"""Meta WhatsApp adapter."""

import hashlib
import hmac
import json
from typing import Any

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field


class QuotedMessage(BaseModel):
    """Represents a quoted/replied-to message."""

    message_id: str = Field(description="ID of the quoted message")
    from_number: str | None = Field(default=None, description="Sender of the quoted message")


class ParsedMessage(BaseModel):
    """Parsed WhatsApp message with standardized structure."""

    id: str | None = None
    from_number: str | None = Field(default=None, alias="from")
    text: str = ""
    type: str = ""
    flow_data: dict[str, Any] | None = None
    media_id: str | None = None
    mime_type: str | None = None
    quoted: QuotedMessage | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)


def parse_payload(payload: dict[str, Any]) -> list[ParsedMessage]:
    """Parse WhatsApp webhook payload and extract messages.

    Args:
        payload: The raw webhook payload from Meta WhatsApp API

    Returns:
        List of ParsedMessage objects with standardized structure
    """
    results: list[ParsedMessage] = []
    entries: list[dict[str, Any]] = payload.get("entry", [])

    for entry in entries:
        changes: list[dict[str, Any]] = entry.get("changes", [])
        for change in changes:
            value: dict[str, Any] = change.get("value", {})
            messages: list[dict[str, Any]] = value.get("messages", []) or []

            for message in messages:
                text = ""
                message_type: str = message.get("type", "")
                flow_data: dict[str, Any] | None = None

                if message_type == "text":
                    text_content: dict[str, Any] = message.get("text", {})
                    text = text_content.get("body", "")
                elif message_type == "image":
                    image: dict[str, Any] = message.get("image", {})
                    text = image.get("caption", "")
                elif message_type == "audio":
                    pass
                elif message_type == "interactive":
                    interactive: dict[str, Any] = message.get("interactive", {})
                    interactive_type: str | None = interactive.get("type")

                    if interactive_type == "flow_completion_message":
                        flow_response: dict[str, Any] = interactive.get("flow_response_payload", {})
                        response_json: str = flow_response.get("response_json", "{}")
                        try:
                            flow_data = json.loads(response_json)
                        except json.JSONDecodeError:
                            flow_data = {"raw": response_json}

                    # Also handle nfm_reply type (WhatsApp Flow PIN/data responses)
                    elif interactive_type == "nfm_reply":
                        nfm_reply: dict[str, Any] = interactive.get("nfm_reply", {})
                        response_json_str: str = nfm_reply.get("response_json", "{}")
                        try:
                            flow_data = json.loads(response_json_str)
                        except json.JSONDecodeError:
                            flow_data = {"raw": response_json_str}

                    # Handle button replies (user clicked a button)
                    elif interactive_type == "button_reply":
                        button_reply: dict[str, Any] = interactive.get("button_reply", {})
                        text = button_reply.get("id", "")  # Button ID becomes the text
                    # Handle list replies (user selected a row from list menu)
                    elif interactive_type == "list_reply":
                        list_reply: dict[str, Any] = interactive.get("list_reply", {})
                        text = list_reply.get("id", "")  # Row ID becomes the text

                media_id = None
                mime_type = None

                if message_type == "image":
                    image_data = message.get("image", {})
                    media_id = image_data.get("id")
                    mime_type = image_data.get("mime_type")
                elif message_type == "audio":
                    audio_data = message.get("audio", {})
                    media_id = audio_data.get("id")
                    mime_type = audio_data.get("mime_type")

                quoted: QuotedMessage | None = None
                context = message.get("context")
                if context:
                    quoted = QuotedMessage(
                        message_id=context.get("id", ""),
                        from_number=context.get("from"),
                    )

                results.append(
                    ParsedMessage(
                        id=message.get("id"),
                        text=text,
                        type=message_type,
                        flow_data=flow_data,
                        media_id=media_id,
                        mime_type=mime_type,
                        quoted=quoted,
                        raw=message,
                    )
                )
    return results


async def verify_meta_signature(request: Request) -> None:
    """Verify Meta signature."""
    app_secret = None
    if not app_secret:
        return
    sig = request.headers.get("X-Hub-Signature-256")
    if not sig or not sig.startswith("sha256="):
        return
    body = await request.body()
    mac = hmac.new(app_secret.encode("utf-8"), msg=body, digestmod=hashlib.sha256)
    expected = "sha256=" + mac.hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise ValueError("Invalid signature")
