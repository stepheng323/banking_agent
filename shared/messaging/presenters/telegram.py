"""Telegram implementation of the Presenter protocol."""

import asyncio
import base64
import html
from typing import Any, cast

from shared.clients.abstractions.messaging import MessagingClient
from shared.clients.telegram.formatting import format_telegram_html
from shared.config.settings import settings
from shared.messaging.intents import (
    RequestAuth,
    RequestConfirmation,
    Say,
    SendTyping,
    ShowFlow,
    ShowOptions,
    ShowReceipt,
    UiIntent,
)
from shared.messaging.presenters.base import PresentationContext, PresentationResult, Presenter
from shared.repositories.unit_of_work import UnitOfWork
from shared.utils.logging import get_logger

logger = get_logger(__name__)
_PIN_PREFIX_TASK_TYPES = {"transfer", "airtime", "data"}


def _actionable_payload(intent: UiIntent) -> dict[str, Any]:
    payload = intent.actionable_payload
    return payload if isinstance(payload, dict) else {}


def _is_schedule_update_confirmation(intent: RequestConfirmation) -> bool:
    payload = _actionable_payload(intent)
    return (
        str(payload.get("task_type") or "").strip().lower() == "schedule"
        and str(payload.get("action") or "").strip().lower() == "edit_scheduled_transaction"
    )


def _pin_prefix_from_payload(payload: dict[str, Any], correlation_id: str) -> str | None:
    task_type = str(payload.get("task_type") or "").strip().lower()
    action = str(payload.get("action") or "").strip().lower()
    if task_type == "schedule" or action in {"edit_scheduled_transaction", "schedule_update"}:
        return "schedule"
    if task_type in _PIN_PREFIX_TASK_TYPES:
        return task_type
    if task_type == "batch":
        tasks = payload.get("tasks")
        if not isinstance(tasks, list):
            return None

        for task in tasks:
            if isinstance(task, dict) and str(task.get("idempotency_key") or "") == correlation_id:
                prefix = _pin_prefix_from_payload(task, correlation_id)
                if prefix:
                    return prefix

        for task in tasks:
            if isinstance(task, dict):
                prefix = _pin_prefix_from_payload(task, correlation_id)
                if prefix:
                    return prefix
    return None


def _pin_flow_prefix(intent: RequestAuth | RequestConfirmation) -> str:
    payload = _actionable_payload(intent)
    payload_prefix = _pin_prefix_from_payload(payload, str(intent.correlation_id or ""))
    if payload_prefix:
        return payload_prefix

    text = f"{getattr(intent, 'reason', '') or ''}\n{getattr(intent, 'header', '') or ''}\n{intent.summary or ''}"
    if "Airtime" in text:
        return "airtime"
    if "Data" in text:
        return "data"
    return "transfer"


class TelegramPresenter(Presenter):
    """Present intents via Telegram Bot API."""

    def __init__(self, messaging_client: MessagingClient) -> None:
        self.client = messaging_client

    @staticmethod
    def _suppress_typing(context: PresentationContext) -> bool:
        # Typing is now handled via explicit 0.0s SendTyping intents from the Orchestrator.
        # Unconditionally suppress the legacy inline pre-send sleep behaviors.
        return True

    @staticmethod
    def _message_kind(intent: UiIntent) -> str:
        if isinstance(intent, Say):
            return "say"
        if isinstance(intent, RequestAuth):
            return "request_auth"
        if isinstance(intent, RequestConfirmation):
            return "request_confirmation"
        if isinstance(intent, ShowReceipt):
            return "show_receipt"
        if isinstance(intent, ShowFlow):
            return "show_flow"
        if isinstance(intent, ShowOptions):
            return "show_options"
        if isinstance(intent, SendTyping):
            return "typing"
        return type(intent).__name__.lower()

    async def _send_typing_before_intent(self, intent: UiIntent, context: PresentationContext) -> None:
        if self._suppress_typing(context):
            return
        logger.info(
            "outbound_typing_indicator_requested",
            channel="telegram",
            message_kind=self._message_kind(intent),
            phone_number=context.phone_number,
            turn_id=context.metadata.get("progress_turn_id") or context.metadata.get("turn_id"),
            inbound_message_id=context.metadata.get("inbound_message_id") or context.metadata.get("message_id"),
            progress_stage=context.metadata.get("progress_stage"),
            typing_requested=True,
        )
        await self.client.send_typing_indicator(context.phone_number)
        delay_seconds = max(0.0, settings.telegram_typing_indicator_delay_ms / 1000)
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)

    async def present(self, intents: list[UiIntent], context: PresentationContext) -> PresentationResult:
        """Render intents to Telegram."""
        result = PresentationResult()

        # Resolve chat_id from phone_number
        async with UnitOfWork() as uow:
            if uow.users:
                identity = await uow.users.get_channel_identity_by_phone(context.phone_number, "telegram")
                if identity:
                    context.phone_number = identity  # override context for downstream calls

        for intent in intents:
            try:
                await self._send_typing_before_intent(intent, context)
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
                elif isinstance(intent, SendTyping):
                    await self._present_typing(intent, context)
                else:
                    logger.warning("unsupported_intent", type=type(intent).__name__)

                if msg_id:
                    result.message_ids.append(msg_id)
            except Exception as e:
                logger.error("telegram_presenter_error", intent=type(intent).__name__, error=str(e))
                result.errors.append(str(e))
                result.success = False

        return result

    async def _present_typing(self, intent: SendTyping, context: PresentationContext) -> None:
        del intent
        await self.client.send_typing_indicator(context.phone_number)

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

        inline_buttons = self._telegram_inline_buttons(intent)
        if inline_buttons:
            resp = await self.client.send_interactive(
                to=context.phone_number,
                body_text=intent.text,
                options=inline_buttons,
                suppress_typing_indicator=self._suppress_typing(context),
            )
            if resp.success:
                return resp.message_id

            logger.info(
                "telegram_say_inline_buttons_fallback_to_text",
                button_count=len(inline_buttons),
            )

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

    @staticmethod
    def _telegram_inline_buttons(intent: Say) -> list[dict[str, str]]:
        payload = intent.actionable_payload if isinstance(intent.actionable_payload, dict) else {}
        raw_buttons = payload.get("telegram_inline_buttons") or payload.get("telegram_inline_keyboard")
        if not isinstance(raw_buttons, list):
            return []

        buttons: list[dict[str, str]] = []
        for item in raw_buttons:
            if not isinstance(item, dict):
                continue
            button_id = str(item.get("id") or item.get("callback_data") or "").strip()
            title = str(item.get("title") or item.get("text") or button_id).strip()
            if button_id and title:
                buttons.append({"id": button_id, "title": title})
        return buttons

    async def _present_auth(self, intent: RequestAuth, context: PresentationContext) -> str | None:
        """Present auth request via Telegram Mini App for secure PIN entry."""
        if intent.method != "pin":
            resp = await self.client.send_text(
                to=context.phone_number,
                text="Authentication method not supported on Telegram.",
                suppress_typing_indicator=self._suppress_typing(context),
            )
            return str(resp.get("message_id")) if isinstance(resp, dict) and "message_id" in resp else None

        prefix = _pin_flow_prefix(intent)
        cta_text = "Authorize Update" if prefix == "schedule" else "Enter PIN"

        flow_token = f"{prefix}-pin-{intent.correlation_id}-{context.phone_number}"
        html_summary = format_telegram_html(intent.summary or "Please enter your PIN to proceed.")

        # Use Mini App for secure PIN entry
        resp = await self.client.send_flow(
            to=context.phone_number,
            flow_id="pin_entry",
            flow_config={
                "header": intent.reason or "Authorize Transaction",
                "text_body": html_summary,
                "flow_cta": cta_text,
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
        if _is_schedule_update_confirmation(intent):
            body = (intent.summary or "").strip()
            prompt = "Reply yes to confirm this schedule update, or no to cancel."
            text = f"{intent.header or 'Confirm Schedule Update'}\n\n{body}\n\n{prompt}" if body else prompt
            resp = await self.client.send_text(
                to=context.phone_number,
                text=text,
                suppress_typing_indicator=self._suppress_typing(context),
            )
            return self._extract_message_id(resp)

        prefix = _pin_flow_prefix(intent)

        flow_token = f"{prefix}-pin-{intent.correlation_id}-{context.phone_number}"
        html_summary = format_telegram_html(intent.summary or "")

        # Use Mini App for PIN-based confirmation
        resp = await self.client.send_flow(
            to=context.phone_number,
            flow_id="pin_entry",
            flow_config={
                "header": intent.header or "Confirm Transaction",
                "text_body": html_summary,
                "flow_cta": "Authorize",
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

    @staticmethod
    def _compact_option_button_title(index: int, option: dict[str, str]) -> str:
        explicit_title = str(option.get("button_title") or "").strip()
        if explicit_title:
            return explicit_title[:64]
        return str(index)

    @staticmethod
    def _telegram_options_body(title: str) -> str:
        return title.strip() or "Choose an option."

    @staticmethod
    def _telegram_options_fallback_body(title: str, options: list[dict[str, str]]) -> str:
        numbered = "\n".join(f"{idx}. {opt['title']}" for idx, opt in enumerate(options, start=1))
        return f"{title.strip()}\n\n{numbered}" if title.strip() else numbered

    async def _present_options(self, intent: ShowOptions, context: PresentationContext) -> str | None:
        """Present options with Telegram inline keyboard and text fallback."""
        options = [
            {
                "id": str(option.get("id", "")).strip() or str(idx),
                "title": str(option.get("title", option.get("label", f"Option {idx}"))),
                "button_title": str(option.get("button_title", "")).strip(),
            }
            for idx, option in enumerate(intent.options, start=1)
            if isinstance(option, dict)
        ]
        if not options:
            return await self._present_say(Say(text=intent.title), context)

        body_text = self._telegram_options_body(intent.title)
        fallback_body_text = self._telegram_options_fallback_body(intent.title, options)
        button_options = [
            {
                "id": opt["id"],
                "title": self._compact_option_button_title(idx, opt),
            }
            for idx, opt in enumerate(options, start=1)
        ]

        logger.info("option_render_mode", channel="telegram", mode="inline_keyboard", option_count=len(options))
        resp = await self.client.send_interactive(
            to=context.phone_number,
            body_text=body_text,
            options=button_options,
            suppress_typing_indicator=self._suppress_typing(context),
        )
        if resp.success:
            return resp.message_id

        # Fallback: plain text while preserving numbered selection path.
        logger.info("option_render_mode", channel="telegram", mode="text", option_count=len(options))
        logger.info("option_fallback_text_used", channel="telegram", option_count=len(options))
        text_resp = await self.client.send_text(
            to=context.phone_number,
            text=fallback_body_text,
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
