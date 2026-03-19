"""Telegram implementation of the Presenter protocol."""

import asyncio
import base64
import html
from typing import Any, cast

from apps.core.src.agent.orchestrator.models.intents import (
    RequestAuth,
    RequestConfirmation,
    Say,
    ShowFlow,
    ShowOptions,
    ShowReceipt,
    UiIntent,
)
from apps.core.src.messaging.presenters.base import PresentationContext, PresentationResult, Presenter
from shared.clients.abstractions.messaging import MessagingClient
from shared.clients.telegram.client import _format_telegram_html
from shared.repositories.unit_of_work import UnitOfWork
from shared.utils.logging import get_logger

logger = get_logger(__name__)
_TYPING_DELAY_SECONDS = 0.6


class TelegramPresenter(Presenter):
    """Present intents via Telegram Bot API."""

    def __init__(self, messaging_client: MessagingClient) -> None:
        self.client = messaging_client

    @staticmethod
    def _suppress_typing(context: PresentationContext) -> bool:
        return bool(context.metadata.get("suppress_typing_indicator", False))

    @staticmethod
    def _typing_delay_seconds(context: PresentationContext) -> float:
        if context.metadata.get("force_typing_indicator"):
            return 0.0
        return _TYPING_DELAY_SECONDS

    async def present(self, intents: list[UiIntent], context: PresentationContext) -> PresentationResult:
        """Render intents to Telegram."""
        result = PresentationResult()

        # Resolve chat_id from phone_number
        async with UnitOfWork() as uow:
            if uow.users:
                identity = await uow.users.get_channel_identity_by_phone(context.phone_number, "telegram")
                if identity:
                    context.phone_number = identity  # override context for downstream calls

        typing_task = self._arm_delayed_typing(intents, context)
        first_send_completed = False

        for intent in intents:
            try:
                msg_id = None
                if isinstance(intent, Say):
                    msg_id = await self._present_say(intent, context)
                elif isinstance(intent, RequestAuth):
                    msg_id = await self._present_auth(intent, context)
                elif isinstance(intent, RequestConfirmation):
                    msg_id = await self._present_confirmation(intent, context)
                elif isinstance(intent, ShowReceipt):
                    msg_id = await self._present_receipt(intent, context)
                elif isinstance(intent, ShowFlow):
                    msg_id = await self._present_flow(intent, context)
                elif isinstance(intent, ShowOptions):
                    msg_id = await self._present_options(intent, context)
                else:
                    logger.warning("unsupported_intent", type=type(intent).__name__)

                if not first_send_completed:
                    await self._cancel_typing_task(typing_task)
                    typing_task = None
                    first_send_completed = True

                if msg_id:
                    result.message_ids.append(msg_id)
            except Exception as e:
                if not first_send_completed:
                    await self._cancel_typing_task(typing_task)
                    typing_task = None
                    first_send_completed = True
                logger.error("telegram_presenter_error", intent=type(intent).__name__, error=str(e))
                result.errors.append(str(e))
                result.success = False

        await self._cancel_typing_task(typing_task)
        return result

    def _arm_delayed_typing(self, intents: list[UiIntent], context: PresentationContext) -> asyncio.Task[None] | None:
        if not intents:
            return None
        if self._suppress_typing(context):
            return None
        if self._first_send_uses_streaming_draft(intents[0], context):
            return None
        return asyncio.create_task(
            self._send_typing_after_delay(
                context.phone_number,
                delay_seconds=self._typing_delay_seconds(context),
            )
        )

    @staticmethod
    async def _cancel_typing_task(task: asyncio.Task[None] | None) -> None:
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _send_typing_after_delay(self, chat_id: str, *, delay_seconds: float) -> None:
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
        await self.client.send_typing_indicator(chat_id)

    def _first_send_uses_streaming_draft(self, intent: UiIntent, context: PresentationContext) -> bool:
        if not isinstance(intent, Say):
            return False
        stream_client = cast(Any, self.client)
        stream_enabled = bool(context.metadata.get("telegram_stream_response", True))
        stream_min_chars = int(context.metadata.get("telegram_stream_min_chars", 48))
        draft_supported = bool(getattr(stream_client, "_draft_supported", True))
        return stream_enabled and draft_supported and len(intent.text.strip()) >= stream_min_chars

    async def _present_say(self, intent: Say, context: PresentationContext) -> str | None:
        if context.metadata.get("request_contact"):
            # Use Telegram-specific _call to send a Reply Keyboard with request_contact=True
            resp = await self.client._call(
                "sendMessage",
                {
                    "chat_id": context.phone_number,
                    "text": intent.text,
                    "reply_markup": {
                        "keyboard": [[{"text": "📱 Share Contact", "request_contact": True}]],
                        "resize_keyboard": True,
                        "one_time_keyboard": True,
                    },
                },
            )
            return self._extract_message_id(resp)

        stream_enabled = bool(context.metadata.get("telegram_stream_response", True))
        stream_min_chars = int(context.metadata.get("telegram_stream_min_chars", 48))
        stream_client = cast(Any, self.client)
        if (
            stream_enabled
            and len(intent.text.strip()) >= stream_min_chars
            and hasattr(stream_client, "send_text_streamed")
        ):
            resp = await stream_client.send_text_streamed(to=context.phone_number, text=intent.text)
        else:
            resp = await self.client.send_text(
                to=context.phone_number,
                text=intent.text,
                suppress_typing_indicator=self._suppress_typing(context),
            )
        return self._extract_message_id(resp)

    async def _present_auth(self, intent: RequestAuth, context: PresentationContext) -> str | None:
        """Present auth request via Telegram Mini App for secure PIN entry."""
        if intent.method != "pin":
            resp = await self.client.send_text(
                to=context.phone_number,
                text="Authentication method not supported on Telegram.",
                suppress_typing_indicator=self._suppress_typing(context),
            )
            return str(resp.get("message_id")) if isinstance(resp, dict) and "message_id" in resp else None

        prefix = "transfer"
        if "Airtime" in (intent.reason or "") or "Airtime" in (intent.summary or ""):
            prefix = "airtime"
        elif "Data" in (intent.reason or "") or "Data" in (intent.summary or ""):
            prefix = "data"

        flow_token = f"{prefix}-pin-{intent.correlation_id}-{context.phone_number}"
        html_summary = _format_telegram_html(intent.summary or "Please enter your PIN to proceed.")

        # Use Mini App for secure PIN entry
        resp = await self.client.send_flow(
            to=context.phone_number,
            flow_id="pin_entry",
            flow_config={
                "header": intent.reason or "Authorize Transaction",
                "text_body": html_summary,
                "flow_cta": "🔐 Enter PIN",
                "flow_token": flow_token,
            },
            suppress_typing_indicator=self._suppress_typing(context),
        )
        return resp.message_id

    async def _present_confirmation(
        self,
        intent: RequestConfirmation,
        context: PresentationContext,
    ) -> str | None:
        """Present confirmation via inline keyboard buttons."""
        prefix = "transfer"
        if "Airtime" in (intent.summary or ""):
            prefix = "airtime"
        elif "Data" in (intent.summary or ""):
            prefix = "data"

        flow_token = f"{prefix}-pin-{intent.correlation_id}-{context.phone_number}"
        html_summary = _format_telegram_html(intent.summary or "")

        # Use Mini App for PIN-based confirmation
        resp = await self.client.send_flow(
            to=context.phone_number,
            flow_id="pin_entry",
            flow_config={
                "header": "Confirm Transaction",
                "text_body": html_summary,
                "flow_cta": "🔐 Authorize",
                "flow_token": flow_token,
            },
            suppress_typing_indicator=self._suppress_typing(context),
        )
        return resp.message_id

    async def _present_receipt(self, intent: ShowReceipt, context: PresentationContext) -> str | None:
        """Present receipt as image or text fallback."""
        receipt_data = intent.receipt

        if "image_base64" in receipt_data:
            mime_type = receipt_data.get("mime_type", "image/png")
            image_bytes = base64.b64decode(receipt_data["image_base64"])
            resp = await self.client.send_image_data(
                to=context.phone_number,
                data=image_bytes,
                caption=intent.caption or "Transaction Receipt",
                mime_type=mime_type,
                suppress_typing_indicator=self._suppress_typing(context),
            )
            return str(resp.get("message_id")) if isinstance(resp, dict) and "message_id" in resp else None

        if "url" in receipt_data:
            resp = await self.client.send_image(
                to=context.phone_number,
                image_url=receipt_data["url"],
                caption=intent.caption or "Transaction Receipt",
                suppress_typing_indicator=self._suppress_typing(context),
            )
            return str(resp.get("message_id")) if isinstance(resp, dict) and "message_id" in resp else None
        else:
            # Text fallback with rich HTML formatting
            lines = [f"🧾 <b>{html.escape(intent.caption or 'Receipt')}</b>"]
            lines.append("────────────────")
            for k, v in receipt_data.items():
                if v:
                    # Clean up keys for display
                    display_key = k.replace("_", " ").title()
                    lines.append(f"<b>{display_key}:</b> <code>{html.escape(str(v))}</code>")
            lines.append("────────────────")
            resp = await self.client.send_text(
                to=context.phone_number,
                text="\n".join(lines),
                suppress_typing_indicator=self._suppress_typing(context),
            )
            return str(resp.get("message_id")) if isinstance(resp, dict) and "message_id" in resp else None

    async def _present_flow(self, intent: ShowFlow, context: PresentationContext) -> str | None:
        """Present flow via Mini App or fallback text."""
        if intent.flow_id:
            resp = await self.client.send_flow(
                to=context.phone_number,
                flow_id=intent.flow_id,
                flow_config=intent.flow_config,
                suppress_typing_indicator=self._suppress_typing(context),
            )
            return resp.message_id
        else:
            fallback = intent.fallback_text or "This action requires flow support."
            resp = await self.client.send_text(
                to=context.phone_number,
                text=fallback,
                suppress_typing_indicator=self._suppress_typing(context),
            )
            return str(resp.get("message_id")) if isinstance(resp, dict) and "message_id" in resp else None

    async def _present_options(self, intent: ShowOptions, context: PresentationContext) -> str | None:
        """Present options with Telegram inline keyboard and text fallback."""
        options = [
            {
                "id": str(option.get("id", "")).strip() or str(idx),
                "title": str(option.get("title", option.get("label", f"Option {idx}"))),
            }
            for idx, option in enumerate(intent.options, start=1)
            if isinstance(option, dict)
        ]
        if not options:
            return await self._present_say(Say(text=intent.title), context)

        logger.info("option_render_mode", channel="telegram", mode="inline_keyboard", option_count=len(options))
        resp = await self.client.send_interactive(
            to=context.phone_number,
            body_text=intent.title,
            options=options,
            suppress_typing_indicator=self._suppress_typing(context),
        )
        if resp.success:
            return resp.message_id

        # Fallback: plain text while preserving numbered selection path.
        numbered = "\n".join(f"{idx}. {opt['title']}" for idx, opt in enumerate(options, start=1))
        logger.info("option_render_mode", channel="telegram", mode="text", option_count=len(options))
        logger.info("option_fallback_text_used", channel="telegram", option_count=len(options))
        fallback_text = f"{intent.title}\n{numbered}"
        text_resp = await self.client.send_text(
            to=context.phone_number,
            text=fallback_text,
            suppress_typing_indicator=self._suppress_typing(context),
        )
        return self._extract_message_id(text_resp)

    @staticmethod
    def _extract_message_id(response: Any) -> str | None:
        if hasattr(response, "message_id"):
            value = response.message_id
            return str(value) if value else None
        if isinstance(response, dict):
            if "message_id" in response and response.get("message_id") is not None:
                return str(response["message_id"])
            nested = response.get("result")
            if isinstance(nested, dict) and nested.get("message_id") is not None:
                return str(nested["message_id"])
        return None
