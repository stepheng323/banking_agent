"""Telegram client for sending messages via the Telegram Bot API."""

import asyncio
import html
import json
import re
from typing import Any

import httpx

from shared.clients.abstractions.messaging import MessageResult, MessagingClient
from shared.config.settings import settings

TELEGRAM_API_BASE = "https://api.telegram.org"


def _format_telegram_html(text: str) -> str:
    """Convert lightweight markdown-like syntax to Telegram-safe HTML."""
    escaped = html.escape(text or "")
    escaped = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*(.+?)\*", r"<b>\1</b>", escaped)

    def _italic_repl(match: re.Match[str]) -> str:
        prefix = match.group(1) or ""
        content = match.group(2) or ""
        return f"{prefix}<i>{content}</i>"

    escaped = re.sub(r"(^|[\s(])_(.+?)_(?=[\s).,!?:;]|$)", _italic_repl, escaped)
    return escaped


class TelegramClient(MessagingClient):
    """Telegram Bot API client implementing the MessagingClient interface."""

    @property
    def channel_name(self) -> str:
        return "telegram"

    @property
    def supports_flows(self) -> bool:
        return False

    def __init__(self) -> None:
        self.bot_token = settings.telegram_bot_token
        self.mini_app_base_url = settings.telegram_mini_app_base_url
        self._validate_config()

    def _validate_config(self) -> None:
        """Validate Telegram client configuration."""
        if not self.bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is not set")

    def _api_url(self, method: str) -> str:
        return f"{TELEGRAM_API_BASE}/bot{self.bot_token}/{method}"

    async def _call(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        """Make a Telegram Bot API call with retries."""
        url = self._api_url(method)
        last_error: Exception | None = None

        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    if files:
                        # multipart upload (photos / documents from bytes)
                        data: dict[str, Any] = payload or {}
                        resp = await client.post(url, data=data, files=files)
                    else:
                        resp = await client.post(url, json=payload)
                    resp.raise_for_status()
                    result: dict[str, Any] = resp.json()

                    if not result.get("ok"):
                        desc = result.get("description", "Unknown error")
                        raise ValueError(f"Telegram API error: {desc}")

                    print(f"✓ Telegram API {method} successful (attempt {attempt})")
                    return result

            except httpx.HTTPStatusError as e:
                last_error = e
                status = e.response.status_code
                if status == 401:
                    print("❌ Telegram API Authentication Failed (401)")
                    print("   Check your TELEGRAM_BOT_TOKEN")
                    raise
                if attempt < max_retries:
                    print(f"⚠️  HTTP {status} (attempt {attempt}/{max_retries}), retrying...")
                    await asyncio.sleep(1 * attempt)
                else:
                    print(f"❌ Max retries reached. Final error: {status}")
                    raise

            except httpx.ConnectError as e:
                last_error = e
                if attempt < max_retries:
                    print(f"⚠️  Connection failed (attempt {attempt}/{max_retries})")
                    await asyncio.sleep(2 * attempt)
                else:
                    print(f"❌ Max retries reached. Connection failed: {e}")
                    raise

            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    print(f"⚠️  Error (attempt {attempt}/{max_retries}): {e}")
                    await asyncio.sleep(1 * attempt)
                else:
                    print(f"❌ Max retries reached: {e}")
                    raise

        if last_error:
            raise last_error
        return {}

    async def send_text(
        self,
        to: str,
        text: str,
        message_id: str | None = None,
    ) -> MessageResult:
        """Send a plain text message via Telegram."""
        if message_id:
            await self.send_typing_indicator(to)

        html_text = _format_telegram_html(text)
        payload: dict[str, Any] = {
            "chat_id": to,
            "text": html_text,
            "parse_mode": "HTML",
        }
        if message_id:
            payload["reply_to_message_id"] = message_id

        try:
            result = await self._call("sendMessage", payload)
            msg_data = result.get("result", {})
            sent_id = str(msg_data.get("message_id", ""))
            return MessageResult(success=True, message_id=sent_id, raw_response=result)
        except Exception as e:
            print(f"❌ Failed to send Telegram text: {e}")
            return MessageResult(success=False, error=str(e))

    async def send_message_draft(self, to: str, text: str) -> bool:
        """Set a draft message in chat using Telegram Bot API sendMessageDraft."""
        draft_text = (text or "").strip()
        if not draft_text:
            return False

        payload: dict[str, Any] = {
            "chat_id": to,
            "text": draft_text[:4096],
        }
        try:
            await self._call("sendMessageDraft", payload, max_retries=1)
            return True
        except Exception as e:
            print(f"⚠️ sendMessageDraft failed: {e}")
            return False

    async def send_text_streamed(
        self,
        to: str,
        text: str,
        message_id: str | None = None,
        *,
        draft_step_chars: int = 120,
        max_draft_updates: int = 12,
        draft_delay_seconds: float = 0.2,
    ) -> MessageResult:
        """Stream a response as Telegram drafts, then publish the final message."""
        clean_text = (text or "").strip()
        if clean_text:
            clipped = clean_text[:4096]
            sent = 0
            cursor = min(len(clipped), max(1, draft_step_chars))
            while cursor < len(clipped) and sent < max_draft_updates:
                await self.send_message_draft(to=to, text=clipped[:cursor])
                sent += 1
                if draft_delay_seconds > 0:
                    await asyncio.sleep(draft_delay_seconds)
                cursor = min(len(clipped), cursor + max(1, draft_step_chars))
            await self.send_message_draft(to=to, text=clipped)

        return await self.send_text(to=to, text=text, message_id=message_id)

    async def send_interactive(
        self,
        to: str,
        body_text: str,
        options: list[dict[str, str]],
        header: str = "",
        footer: str = "",
        message_id: str | None = None,
    ) -> MessageResult:
        """Send an interactive message with inline keyboard buttons."""
        if message_id:
            await self.send_typing_indicator(to)

        parts: list[str] = []
        if header:
            parts.append(f"*{header}*")
        parts.append(body_text)
        if footer:
            parts.append(f"_{footer}_")

        # Build inline keyboard — one button per row
        keyboard_rows = [
            [{"text": opt.get("title", opt.get("id", "Option")), "callback_data": opt.get("id", "")}] for opt in options
        ]

        combined_text = "\n\n".join(parts)
        html_text = _format_telegram_html(combined_text)

        payload: dict[str, Any] = {
            "chat_id": to,
            "text": html_text,
            "parse_mode": "HTML",
            "reply_markup": json.dumps({"inline_keyboard": keyboard_rows}),
        }

        try:
            result = await self._call("sendMessage", payload)
            msg_data = result.get("result", {})
            sent_id = str(msg_data.get("message_id", ""))
            return MessageResult(success=True, message_id=sent_id, raw_response=result)
        except Exception as e:
            print(f"❌ Failed to send Telegram interactive: {e}")
            return MessageResult(success=False, error=str(e))

    async def send_image(
        self,
        to: str,
        image_url: str,
        caption: str = "",
        message_id: str | None = None,
    ) -> MessageResult:
        """Send an image by URL."""
        if message_id:
            await self.send_typing_indicator(to)

        payload: dict[str, Any] = {
            "chat_id": to,
            "photo": image_url,
        }
        if caption:
            payload["caption"] = caption

        try:
            result = await self._call("sendPhoto", payload)
            msg_data = result.get("result", {})
            sent_id = str(msg_data.get("message_id", ""))
            return MessageResult(success=True, message_id=sent_id, raw_response=result)
        except Exception as e:
            print(f"❌ Failed to send Telegram image: {e}")
            return MessageResult(success=False, error=str(e))

    async def send_image_data(
        self,
        to: str,
        data: bytes,
        caption: str = "",
        mime_type: str = "image/png",
        message_id: str | None = None,
    ) -> MessageResult:
        """Send an image from bytes via multipart upload."""
        if message_id:
            await self.send_typing_indicator(to)

        ext = mime_type.split("/")[-1]
        filename = f"image.{ext}"

        form_data: dict[str, Any] = {"chat_id": to}
        if caption:
            form_data["caption"] = caption

        files_payload = {"photo": (filename, data, mime_type)}

        try:
            result = await self._call("sendPhoto", payload=form_data, files=files_payload)
            msg_data = result.get("result", {})
            sent_id = str(msg_data.get("message_id", ""))
            return MessageResult(success=True, message_id=sent_id, raw_response=result)
        except Exception as e:
            print(f"❌ Failed to send Telegram image data: {e}")
            return MessageResult(success=False, error=str(e))

    async def send_typing_indicator(self, chat_id: str) -> bool:
        """Send typing indicator (chat action)."""
        try:
            await self._call(
                "sendChatAction",
                {"chat_id": chat_id, "action": "typing"},
                max_retries=1,
            )
            return True
        except Exception as e:
            print(f"⚠️ Typing indicator failed: {e}")
            return False

    async def send_document(
        self,
        to: str,
        data: bytes,
        filename: str,
        caption: str = "",
        mime_type: str = "application/pdf",
        message_id: str | None = None,
    ) -> MessageResult:
        """Send a document via multipart upload."""
        if message_id:
            await self.send_typing_indicator(to)

        form_data: dict[str, Any] = {"chat_id": to}
        if caption:
            form_data["caption"] = caption

        files_payload = {"document": (filename, data, mime_type)}

        try:
            result = await self._call("sendDocument", payload=form_data, files=files_payload)
            msg_data = result.get("result", {})
            sent_id = str(msg_data.get("message_id", ""))
            return MessageResult(success=True, message_id=sent_id, raw_response=result)
        except Exception as e:
            print(f"❌ Failed to send Telegram document: {e}")
            return MessageResult(success=False, error=str(e))

    async def send_flow(
        self,
        to: str,
        flow_id: str,
        flow_config: dict[str, Any],
        message_id: str | None = None,
    ) -> MessageResult:
        """Send a flow via Telegram Mini App.

        Opens a Mini App (web_app button) for secure data entry.
        Falls back to text if no Mini App base URL is configured.
        """
        return await self.send_mini_app(
            to=to,
            flow_token=flow_config.get("flow_token", ""),
            header=flow_config.get("header", ""),
            body_text=flow_config.get("text_body", ""),
            cta_text=flow_config.get("flow_cta", "Open"),
            message_id=message_id,
        )

    async def send_mini_app(
        self,
        to: str,
        flow_token: str,
        header: str = "",
        body_text: str = "",
        cta_text: str = "Open",
        message_id: str | None = None,
    ) -> MessageResult:
        """Send an inline keyboard button that opens a Telegram Mini App.

        Used for secure PIN entry and other flow-like interactions.
        """
        if not self.mini_app_base_url:
            # No Mini App configured — send a text fallback
            return await self.send_text(
                to=to,
                text=body_text or "This action requires a Mini App. Please contact support.",
                message_id=message_id,
            )

        if message_id:
            await self.send_typing_indicator(to)

        endpoint = "onboarding.html" if "onboarding" in flow_token else "pin_entry.html"
        import time

        mini_app_url = (
            f"{self.mini_app_base_url}/static/telegram/{endpoint}"
            f"?flow_token={flow_token}&chat_id={to}&v={int(time.time())}"
        )

        parts: list[str] = []
        if header:
            parts.append(f"<b>{header}</b>")
        if body_text:
            parts.append(body_text)

        keyboard = {"inline_keyboard": [[{"text": cta_text, "web_app": {"url": mini_app_url}}]]}

        payload: dict[str, Any] = {
            "chat_id": to,
            "text": "\n\n".join(parts) or "Please tap the button below.",
            "parse_mode": "HTML",
            "reply_markup": json.dumps(keyboard),
        }

        try:
            result = await self._call("sendMessage", payload)
            msg_data = result.get("result", {})
            sent_id = str(msg_data.get("message_id", ""))

            # Store the message_id in Redis so pin_submit can later remove the button
            if sent_id and flow_token:
                try:
                    from shared.cache.redis_client import RedisClient

                    rc = RedisClient.get_client()
                    await rc.setex(f"tg:pin_msg:{flow_token}", 1800, sent_id)
                except Exception as e:
                    print(f"⚠️ Could not cache PIN message_id: {e}")

            return MessageResult(success=True, message_id=sent_id, raw_response=result)
        except Exception as e:
            print(f"❌ Failed to send Telegram Mini App: {e}")
            return MessageResult(success=False, error=str(e))

    async def get_media_url(self, media_id: str) -> str:
        """Get the URL for a Telegram file."""
        try:
            result = await self._call("getFile", {"file_id": media_id})
            file_path = result.get("result", {}).get("file_path")
            if not file_path:
                raise ValueError(f"Could not get file_path for media {media_id}")

            return f"https://api.telegram.org/file/bot{self.bot_token}/{file_path}"
        except Exception as e:
            print(f"❌ Failed getting Telegram media URL: {e}")
            raise

    async def download_media(self, media_url: str) -> bytes:
        """Download media bytes from Telegram."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(media_url)
                resp.raise_for_status()
                return resp.content
        except Exception as e:
            print(f"❌ Failed downloading Telegram media: {e}")
            raise

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str = "",
    ) -> bool:
        """Answer a callback query (acknowledge inline button press)."""
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text

        try:
            await self._call("answerCallbackQuery", payload, max_retries=1)
            return True
        except Exception as e:
            print(f"⚠️ answerCallbackQuery failed: {e}")
            return False

    async def mark_as_authorized(self, chat_id: str, message_id: str | int) -> bool:
        """Replace the PIN Web App button with a non-interactive 'Authorized' badge.

        Called after a successful PIN auth so the user sees confirmation but
        cannot re-open the Mini App.
        """
        try:
            await self._call(
                "editMessageReplyMarkup",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "reply_markup": {"inline_keyboard": [[{"text": "✓ Authorized", "callback_data": "auth:done"}]]},
                },
                max_retries=1,
            )
            return True
        except Exception as e:
            print(f"⚠️ mark_as_authorized failed: {e}")
            return False

    async def set_webhook(self, webhook_url: str) -> bool:
        """Register the webhook URL with Telegram."""
        try:
            result = await self._call("setWebhook", {"url": webhook_url})
            print(f"✓ Telegram webhook set to {webhook_url}")
            return result.get("ok", False)
        except Exception as e:
            print(f"❌ Failed to set webhook: {e}")
            return False

    async def set_my_commands(self, commands: list[dict[str, str]]) -> bool:
        """Register the persistent bot menu commands.
        commands should be a list like: [{"command": "start", "description": "Start the bot"}]
        """
        try:
            result = await self._call("setMyCommands", {"commands": commands})
            print("✓ Telegram bot commands updated successfully")
            return result.get("ok", False)
        except Exception as e:
            print(f"❌ Failed to set bot commands: {e}")
            return False
