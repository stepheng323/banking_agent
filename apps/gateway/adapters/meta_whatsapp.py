from typing import Dict, List
import hmac
import hashlib
from fastapi import Request
from apps.gateway.core.config import settings


def parse_payload(payload: Dict) -> List[Dict]:
    results: List[Dict] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []) or []:
                text = ""
                if message.get("type") == "text":
                    text = message.get("text", {}).get("body", "")
                results.append(
                    {
                        "id": message.get("id"),
                        "from": message.get("from"),
                        "text": text,
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
