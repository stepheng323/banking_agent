"""Telegram implementation of the Presenter protocol."""

import base64
import html

from apps.core.src.agent.orchestrator.models.intents import (
    RequestAuth,
    RequestConfirmation,
    Say,
    ShowFlow,
    ShowReceipt,
    UiIntent,
)
from apps.core.src.messaging.presenters.base import PresentationContext, PresentationResult, Presenter
from shared.clients.abstractions.messaging import MessagingClient
from shared.repositories.unit_of_work import UnitOfWork
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class TelegramPresenter(Presenter):
    """Present intents via Telegram Bot API."""

    def __init__(self, messaging_client: MessagingClient) -> None:
        self.client = messaging_client

    async def present(self, intents: list[UiIntent], context: PresentationContext) -> PresentationResult:
        """Render intents to Telegram."""
        result = PresentationResult()

        # Resolve chat_id from phone_number
        async with UnitOfWork() as uow:
            if uow.users:
                identity = await uow.users.get_channel_identity_by_phone(context.phone_number, "telegram")
                if identity:
                    context.phone_number = identity  # override context for downstream calls

        # Fire typing indicator immediately so the user sees activity
        await self.client.send_typing_indicator(context.phone_number)

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
                else:
                    logger.warning("unsupported_intent", type=type(intent).__name__)

                if msg_id:
                    result.message_ids.append(msg_id)
            except Exception as e:
                logger.error("telegram_presenter_error", intent=type(intent).__name__, error=str(e))
                result.errors.append(str(e))
                result.success = False

        return result

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
            return str(resp.get("message_id")) if isinstance(resp, dict) and "message_id" in resp else None
        else:
            resp = await self.client.send_text(
                to=context.phone_number,
                text=intent.text,
            )
            return str(resp.get("message_id")) if isinstance(resp, dict) and "message_id" in resp else None

    async def _present_auth(self, intent: RequestAuth, context: PresentationContext) -> str | None:
        """Present auth request via Telegram Mini App for secure PIN entry."""
        if intent.method != "pin":
            resp = await self.client.send_text(
                to=context.phone_number,
                text="Authentication method not supported on Telegram.",
            )
            return str(resp.get("message_id")) if isinstance(resp, dict) and "message_id" in resp else None

        prefix = "transfer"
        if "Airtime" in (intent.reason or "") or "Airtime" in (intent.summary or ""):
            prefix = "airtime"
        elif "Data" in (intent.reason or "") or "Data" in (intent.summary or ""):
            prefix = "data"

        flow_token = f"{prefix}-pin-{intent.correlation_id}-{context.phone_number}"

        import re

        html_summary = intent.summary or "Please enter your PIN to proceed."
        html_summary = html.escape(html_summary)
        # Convert simple markdown bold (*) to html (<b>)
        html_summary = re.sub(r"\*(.*?)\*", r"<b>\1</b>", html_summary)

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

        import re

        html_summary = intent.summary or ""
        html_summary = html.escape(html_summary)
        # Convert simple markdown bold (*) to html (<b>)
        html_summary = re.sub(r"\*(.*?)\*", r"<b>\1</b>", html_summary)

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
            )
            return str(resp.get("message_id")) if isinstance(resp, dict) and "message_id" in resp else None

        if "url" in receipt_data:
            resp = await self.client.send_image(
                to=context.phone_number,
                image_url=receipt_data["url"],
                caption=intent.caption or "Transaction Receipt",
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
            )
            return str(resp.get("message_id")) if isinstance(resp, dict) and "message_id" in resp else None

    async def _present_flow(self, intent: ShowFlow, context: PresentationContext) -> str | None:
        """Present flow via Mini App or fallback text."""
        if intent.flow_id:
            resp = await self.client.send_flow(
                to=context.phone_number,
                flow_id=intent.flow_id,
                flow_config=intent.flow_config,
            )
            return resp.message_id
        else:
            fallback = intent.fallback_text or "This action requires flow support."
            resp = await self.client.send_text(to=context.phone_number, text=fallback)
            return str(resp.get("message_id")) if isinstance(resp, dict) and "message_id" in resp else None
