"""Register the Telegram webhook URL for local or deployed gateway testing."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv


def _load_env() -> None:
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env", override=False)


def _webhook_url(public_base_url: str) -> str:
    return public_base_url.rstrip("/") + "/webhook/telegram"


def _payload(url: str, *, secret_token: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "url": url,
        "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": True,
    }
    if secret_token:
        payload["secret_token"] = secret_token
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Set Telegram Bot webhook to this app's Telegram ingress endpoint.")
    parser.add_argument(
        "public_base_url",
        help="Public HTTPS base URL that tunnels to local gateway, for example https://abc123.ngrok-free.app",
    )
    parser.add_argument(
        "--keep-pending",
        action="store_true",
        help="Do not drop pending Telegram updates when switching the webhook.",
    )
    args = parser.parse_args()

    _load_env()
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    secret_token = os.getenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "").strip()
    if not bot_token:
        print("Missing TELEGRAM_BOT_TOKEN in .env", file=sys.stderr)
        return 1

    url = _webhook_url(args.public_base_url)
    payload = _payload(url, secret_token=secret_token)
    if args.keep_pending:
        payload["drop_pending_updates"] = False

    response = httpx.post(f"https://api.telegram.org/bot{bot_token}/setWebhook", json=payload, timeout=20.0)
    response.raise_for_status()
    body = response.json()
    if not body.get("ok"):
        print(body, file=sys.stderr)
        return 1

    print(f"Telegram webhook set to {url}")
    if secret_token:
        print("Secret token enabled.")
    else:
        print(
            "Warning: TELEGRAM_WEBHOOK_SECRET_TOKEN is empty; "
            "local endpoint will not verify Telegram's secret header."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
