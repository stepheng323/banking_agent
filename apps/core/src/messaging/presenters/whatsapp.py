"""WhatsApp implementation of the Presenter protocol."""

import base64

from apps.core.src.agent.orchestrator.models.intents import (
    RequestAuth,
    RequestConfirmation,
    Say,
    ShowFlow,
    ShowOptions,
    ShowReceipt,
    SendTyping,
    UiIntent,
)
from apps.core.src.messaging.presenters.base import PresentationContext, PresentationResult, Presenter
from shared.clients.abstractions.messaging import MessagingClient
from shared.config.settings import settings
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class WhatsAppPresenter(Presenter):
    """Present intents via WhatsApp."""

    def __init__(self, messaging_client: MessagingClient):
        self.client = messaging_client

    @staticmethod
    def _suppress_typing(context: PresentationContext) -> bool:
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

    @classmethod
    def _log_typing_request(cls, intent: UiIntent, context: PresentationContext) -> None:
        if cls._suppress_typing(context):
            return
        logger.info(
            "outbound_typing_indicator_requested",
            channel="whatsapp",
            message_kind=cls._message_kind(intent),
            phone_number=context.phone_number,
            turn_id=context.metadata.get("progress_turn_id") or context.metadata.get("turn_id"),
            inbound_message_id=context.metadata.get("inbound_message_id") or context.metadata.get("message_id"),
            progress_stage=context.metadata.get("progress_stage"),
            typing_requested=True,
        )

    async def present(self, intents: list[UiIntent], context: PresentationContext) -> PresentationResult:
        """Render intents to WhatsApp."""
        result = PresentationResult()

        for intent in intents:
            try:
                self._log_typing_request(intent, context)
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
                logger.error("presenter_error", intent=type(intent), error=str(e))
                result.errors.append(str(e))
                result.success = False

        return result

    async def _present_typing(self, intent: SendTyping, context: PresentationContext) -> None:
        msg_id = context.metadata.get("inbound_message_id")
        await self.client.send_typing_indicator(msg_id)

    async def _present_say(self, intent: Say, context: PresentationContext) -> str | None:
        resp = await self.client.send_text(
            to=context.phone_number,
            text=intent.text,
            suppress_typing_indicator=self._suppress_typing(context),
        )
        return resp.get("messages", [{}])[0].get("id") if isinstance(resp, dict) else None

    async def _present_auth(self, intent: RequestAuth, context: PresentationContext) -> str | None:
        supports_flows = context.capabilities.get("flows", False)

        if intent.method == "pin" and supports_flows:
            header = intent.reason or "Authorize Transaction"
            cta = "Authorize"

            prefix = "transfer"
            if "Airtime" in header or "Airtime" in (intent.summary or ""):
                prefix = "airtime"
            elif "Data" in header or "Data" in (intent.summary or ""):
                prefix = "data"

            if "Transfer" in header:
                cta = "Authorize Transfer"

            resp = await self.client.send_flow(
                to=context.phone_number,
                flow_id=settings.pin_confirmation_flow_id,
                flow_config={
                    "header": header,
                    "text_body": intent.summary,
                    "flow_cta": cta,
                    "screen_name": "Pin",
                    "flow_token": f"{prefix}-pin-{intent.correlation_id}-{context.phone_number}",
                    "flow_action_payload": {
                        "screen": "Pin",
                    },
                },
                suppress_typing_indicator=self._suppress_typing(context),
            )
            return resp.message_id
        else:
            logger.warning("auth_flow_unsupported", phone=context.phone_number)
            resp = await self.client.send_text(
                to=context.phone_number,
                text="Secure transaction requires WhatsApp Flows support. Please update your WhatsApp version.",
                suppress_typing_indicator=self._suppress_typing(context),
            )
            return resp.get("messages", [{}])[0].get("id") if isinstance(resp, dict) else None

    async def _present_confirmation(self, intent: RequestConfirmation, context: PresentationContext) -> str | None:
        supports_flows = context.capabilities.get("flows", False)

        if supports_flows:
            # Dynamic Prefix logic
            prefix = "transfer"
            if "Airtime" in (intent.summary or ""):
                prefix = "airtime"
            elif "Data" in (intent.summary or ""):
                prefix = "data"

            resp = await self.client.send_flow(
                to=context.phone_number,
                flow_id=settings.pin_confirmation_flow_id,
                flow_config={
                    "header": "Confirm Transaction",
                    "text_body": intent.summary,
                    "flow_cta": "Authorize",
                    "screen_name": "Pin",
                    "flow_token": f"{prefix}-pin-{intent.correlation_id}-{context.phone_number}",
                    "flow_action_payload": {
                        "screen": "Pin",
                    },
                },
                suppress_typing_indicator=self._suppress_typing(context),
            )
            return resp.message_id
        else:
            logger.warning("confirmation_flow_unsupported", phone=context.phone_number)
            resp = await self.client.send_text(
                to=context.phone_number,
                text="Confirmation requires WhatsApp Flows support. Please update your WhatsApp version.",
                suppress_typing_indicator=self._suppress_typing(context),
            )
            return resp.get("messages", [{}])[0].get("id") if isinstance(resp, dict) else None

    async def _present_receipt(self, intent: ShowReceipt, context: PresentationContext) -> str | None:
        # Decide: Image vs Text
        # For now, let's assume if we have a receipt dict, we want to try generic text or specialized renderer.
        # Since I don't have access to the PDF renderer here directly, I might rely on pre-generated URLs or just text.

        # NOTE: The Orchestrator's ShowReceipt currently provides raw data.
        # Ideally, we'd generate an image. For this pass, let's send a rich text summary
        # or if 'url' is in the receipt wrapper, send image.

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
            return resp.get("messages", [{}])[0].get("id") if isinstance(resp, dict) else None

        if "url" in receipt_data:
            resp = await self.client.send_image(
                to=context.phone_number,
                image_url=receipt_data["url"],
                caption=intent.caption or "Transaction Receipt",
                suppress_typing_indicator=self._suppress_typing(context),
            )
            return resp.get("messages", [{}])[0].get("id") if isinstance(resp, dict) else None
        else:
            # Fallback text summary
            lines = [f"🧾 *{intent.caption or 'Receipt'}*"]
            for k, v in receipt_data.items():
                if v:
                    lines.append(f"*{k}:* {v}")
            resp = await self.client.send_text(
                to=context.phone_number,
                text="\n".join(lines),
                suppress_typing_indicator=self._suppress_typing(context),
            )
            return resp.get("messages", [{}])[0].get("id") if isinstance(resp, dict) else None

    async def _present_flow(self, intent: ShowFlow, context: PresentationContext) -> str | None:
        supports_flows = context.capabilities.get("flows", False)
        if supports_flows and intent.flow_id:
            resp = await self.client.send_flow(
                to=context.phone_number,
                flow_id=intent.flow_id,
                flow_config=intent.flow_config,
                suppress_typing_indicator=self._suppress_typing(context),
            )
            return resp.message_id
        else:
            fallback = intent.fallback_text or "This action requires flow support on your channel."
            resp = await self.client.send_text(
                to=context.phone_number,
                text=fallback,
                suppress_typing_indicator=self._suppress_typing(context),
            )
            return resp.get("messages", [{}])[0].get("id") if isinstance(resp, dict) else None

    async def _present_options(self, intent: ShowOptions, context: PresentationContext) -> str | None:
        """Present options with channel-native interactive UI and text fallback."""
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

        # WhatsApp supports buttons (<=3) and list menus (<=10) through send_interactive.
        if len(options) <= 10:
            mode = "button" if len(options) <= 3 else "list"
            logger.info("option_render_mode", channel="whatsapp", mode=mode, option_count=len(options))
            interactive_resp = await self.client.send_interactive(
                to=context.phone_number,
                body_text=intent.title,
                options=options,
                suppress_typing_indicator=self._suppress_typing(context),
            )
            if interactive_resp.success:
                return interactive_resp.message_id
            logger.warning("whatsapp_show_options_interactive_failed", error=interactive_resp.error)

        numbered = "\n".join(f"{idx}. {opt['title']}" for idx, opt in enumerate(options, start=1))
        logger.info("option_render_mode", channel="whatsapp", mode="text", option_count=len(options))
        logger.info("option_fallback_text_used", channel="whatsapp", option_count=len(options))
        fallback_text = f"{intent.title}\n{numbered}"
        text_resp = await self.client.send_text(
            to=context.phone_number,
            text=fallback_text,
            suppress_typing_indicator=self._suppress_typing(context),
        )
        return text_resp.get("messages", [{}])[0].get("id") if isinstance(text_resp, dict) else None
