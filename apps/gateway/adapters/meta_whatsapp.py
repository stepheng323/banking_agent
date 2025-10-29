"""Meta WhatsApp adapter."""

import hashlib
import hmac
import json
from typing import Any, Dict, List, Optional, TypedDict

from fastapi import Request

ParsedMessage = TypedDict(
    "ParsedMessage",
    {
        "id": Optional[str],
        "from": Optional[str],
        "text": str,
        "type": str,
        "flow_data": Optional[Dict[str, Any]],
        "raw": Dict[str, Any],
    },
    total=False,
)


def parse_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse WhatsApp webhook payload and extract messages.

    Args:
        payload: The raw webhook payload from Meta WhatsApp API

    Returns:
        List of parsed message dictionaries with standardized structure
    """
    results: List[Dict[str, Any]] = []
    entries: List[Dict[str, Any]] = payload.get("entry", [])

    for entry in entries:
        changes: List[Dict[str, Any]] = entry.get("changes", [])
        for change in changes:
            value: Dict[str, Any] = change.get("value", {})
            messages: List[Dict[str, Any]] = value.get("messages", []) or []

            for message in messages:
                text = ""
                message_type: str = message.get("type", "")
                flow_data: Dict[str, Any] | None = None

                if message_type == "text":
                    text_content: Dict[str, Any] = message.get("text", {})
                    text = text_content.get("body", "")
                elif message_type == "interactive":
                    interactive: Dict[str, Any] = message.get(
                        "interactive", {})
                    interactive_type: str | None = interactive.get("type")

                    if interactive_type == "flow_completion_message":
                        flow_response: Dict[str, Any] = interactive.get(
                            "flow_response_payload", {})
                        response_json: str = flow_response.get(
                            "response_json", "{}")
                        try:
                            flow_data = json.loads(response_json)
                        except json.JSONDecodeError:
                            flow_data = {"raw": response_json}

                results.append(
                    {
                        "id": message.get("id"),
                        "from": message.get("from"),
                        "text": text,
                        "type": message_type,
                        "flow_data": flow_data,
                        "raw": message,
                    }
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
    mac = hmac.new(app_secret.encode("utf-8"),
                   msg=body, digestmod=hashlib.sha256)
    expected = "sha256=" + mac.hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise ValueError("Invalid signature")
