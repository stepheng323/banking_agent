from typing import Dict, List
import hmac
import hashlib
import json
from fastapi import Request
from apps.gateway.core.config import settings


def parse_payload(payload: Dict) -> List[Dict]:
    results: List[Dict] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []) or []:
                text = ""
                message_type = message.get("type", "")
                flow_data = None

                if message_type == "text":
                    text = message.get("text", {}).get("body", "")
                elif message_type == "interactive":
                    interactive = message.get("interactive", {})
                    interactive_type = interactive.get("type")

                    if interactive_type == "flow_completion_message":
                        # Extract flow response data
                        flow_response = interactive.get("flow_response_payload", {})
                        response_json = flow_response.get("response_json", "{}")
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
    app_secret = None  # Set to your app secret if you want verification
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
