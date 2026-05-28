"""Telegram Mini App message construction and session bookkeeping."""

import time
from typing import Any, Protocol
from urllib.parse import urlencode

from shared.cache.redis_client import RedisClient
from shared.clients.abstractions.messaging import MessageResult
from shared.services.telegram_miniapp_bootstrap import create_telegram_miniapp_bootstrap
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)


class TelegramApiCall(Protocol):
    async def __call__(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        max_retries: int = 3,
    ) -> dict[str, Any]: ...


def route_for_flow_token(flow_token: str) -> tuple[str, str]:
    if flow_token.startswith("link-"):
        return "linking.html", "linking"
    if "onboarding" in flow_token:
        return "onboarding.html", "onboarding"
    return "pin_entry.html", "pin"


def build_mini_app_message_payload(
    *,
    to: str,
    mini_app_base_url: str,
    endpoint: str,
    bootstrap_nonce: str,
    header: str = "",
    body_text: str = "",
    cta_text: str = "Open",
) -> dict[str, Any]:
    query_params = {"boot": bootstrap_nonce, "v": str(int(time.time()))}
    mini_app_url = f"{mini_app_base_url}/static/telegram/{endpoint}?{urlencode(query_params)}"

    parts: list[str] = []
    if header:
        parts.append(f"<b>{header}</b>")
    if body_text:
        parts.append(body_text)

    return {
        "chat_id": to,
        "text": "\n\n".join(parts) or "Please tap the button below.",
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": [[{"text": cta_text, "web_app": {"url": mini_app_url}}]]},
    }


async def send_mini_app_message(
    *,
    api_call: TelegramApiCall,
    mini_app_base_url: str,
    to: str,
    flow_token: str,
    header: str = "",
    body_text: str = "",
    cta_text: str = "Open",
) -> MessageResult:
    endpoint, bootstrap_endpoint = route_for_flow_token(flow_token)
    bootstrap_extra: dict[str, Any] = {}
    if bootstrap_endpoint == "pin" and cta_text and cta_text != "Open":
        bootstrap_extra["submit_label"] = str(cta_text)

    try:
        bootstrap_nonce = await create_telegram_miniapp_bootstrap(
            chat_id=to,
            flow_token=flow_token,
            endpoint=bootstrap_endpoint,
            extra=bootstrap_extra,
        )
    except Exception as e:
        logger.error(
            "telegram_mini_app_bootstrap_create_failed",
            error_type=type(e).__name__,
            chat_id_hash=log_fingerprint(to),
            flow_token_hash=log_fingerprint(flow_token),
            endpoint=bootstrap_endpoint,
        )
        return MessageResult(success=False, error="Failed to create secure Mini App session")

    payload = build_mini_app_message_payload(
        to=to,
        mini_app_base_url=mini_app_base_url,
        endpoint=endpoint,
        bootstrap_nonce=bootstrap_nonce,
        header=header,
        body_text=body_text,
        cta_text=cta_text,
    )

    try:
        result = await api_call("sendMessage", payload)
        msg_data = result.get("result", {})
        sent_id = str(msg_data.get("message_id", ""))
        await cache_pin_message_id(flow_token=flow_token, message_id=sent_id)
        return MessageResult(success=True, message_id=sent_id, raw_response=result)
    except Exception as e:
        logger.error("telegram_mini_app_send_failed", to_hash=log_fingerprint(to), error_type=type(e).__name__)
        return MessageResult(success=False, error="Telegram Mini App send failed")


async def cache_pin_message_id(*, flow_token: str, message_id: str) -> None:
    if not message_id or not flow_token:
        return

    try:
        redis_client = RedisClient.get_client()
        await redis_client.setex(f"tg:pin_msg:{flow_token}", 1800, message_id)
    except Exception as e:
        logger.warning(
            "telegram_pin_message_cache_failed",
            flow_token_hash=log_fingerprint(flow_token),
            error_type=type(e).__name__,
        )
