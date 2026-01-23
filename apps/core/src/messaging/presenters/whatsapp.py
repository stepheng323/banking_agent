"""WhatsApp implementation of the Presenter protocol."""

from apps.core.src.agent.orchestrator.intents import (
    RequestAuth,
    RequestConfirmation,
    Say,
    ShowFlow,
    ShowReceipt,
    UiIntent,
)
from apps.core.src.messaging.presenters.base import PresentationContext, Presenter
from shared.clients.abstractions.messaging import MessagingClient
from shared.config.settings import settings
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class WhatsAppPresenter(Presenter):
    """Present intents via WhatsApp."""

    def __init__(self, messaging_client: MessagingClient):
        self.client = messaging_client

    async def present(self, intents: list[UiIntent], context: PresentationContext) -> None:
        """Render intents to WhatsApp."""
        for intent in intents:
            try:
                if isinstance(intent, Say):
                    await self._present_say(intent, context)
                elif isinstance(intent, RequestAuth):
                    await self._present_auth(intent, context)
                elif isinstance(intent, RequestConfirmation):
                    await self._present_confirmation(intent, context)
                elif isinstance(intent, ShowReceipt):
                    await self._present_receipt(intent, context)
                elif isinstance(intent, ShowFlow):
                    await self._present_flow(intent, context)
                # Future: Handle other intents (Ask, ShowOptions, etc.)
                else:
                    logger.warning("unsupported_intent", type=type(intent).__name__)
            except Exception as e:
                logger.error("presenter_error", intent=type(intent), error=str(e))

    async def _present_say(self, intent: Say, context: PresentationContext) -> None:
        await self.client.send_text(
            to=context.phone_number,
            text=intent.text,
        )

    async def _present_auth(self, intent: RequestAuth, context: PresentationContext) -> None:
        supports_flows = context.capabilities.get("flows", False)

        if intent.method == "pin" and supports_flows:
            header = intent.reason or "Authorize Transaction"
            cta = "Authorize"

            if "Transfer" in header:
                cta = "Authorize Transfer"

            await self.client.send_flow(
                to=context.phone_number,
                flow_id=settings.pin_confirmation_flow_id,
                flow_config={
                    "header": header,
                    "text_body": intent.summary,
                    "flow_cta": cta,
                    "screen_name": "Pin",
                    "flow_token": f"transfer-pin-{intent.correlation_id}-{context.phone_number}",
                    "flow_action_payload": {
                        "screen": "Pin",
                    },
                },
            )
        else:
            logger.warning("auth_flow_unsupported", phone=context.phone_number)
            await self.client.send_text(
                to=context.phone_number,
                text="Secure transaction requires WhatsApp Flows support. Please update your WhatsApp version.",
            )

    async def _present_confirmation(self, intent: RequestConfirmation, context: PresentationContext) -> None:
        supports_flows = context.capabilities.get("flows", False)

        if supports_flows:
            await self.client.send_flow(
                to=context.phone_number,
                flow_id=settings.pin_confirmation_flow_id,
                flow_config={
                    "header": "Confirm Transaction",
                    "text_body": intent.summary,
                    "flow_cta": "Authorize",
                    "screen_name": "Pin",
                    "flow_token": f"transfer-pin-{intent.correlation_id}-{context.phone_number}",
                    "flow_action_payload": {
                        "screen": "Pin",
                    },
                },
            )
        else:
            logger.warning("confirmation_flow_unsupported", phone=context.phone_number)
            await self.client.send_text(
                to=context.phone_number,
                text="Confirmation requires WhatsApp Flows support. Please update your WhatsApp version.",
            )

    async def _present_receipt(self, intent: ShowReceipt, context: PresentationContext) -> None:
        # Decide: Image vs Text
        # For now, let's assume if we have a receipt dict, we want to try generic text or specialized renderer.
        # Since I don't have access to the PDF renderer here directly, I might rely on pre-generated URLs or just text.

        # NOTE: The Orchestrator's ShowReceipt currently provides raw data.
        # Ideally, we'd generate an image. For this pass, let's send a rich text summary
        # or if 'url' is in the receipt wrapper, send image.

        receipt_data = intent.receipt

        if "url" in receipt_data:
            await self.client.send_image(
                to=context.phone_number,
                image_url=receipt_data["url"],
                caption=intent.caption or "Transaction Receipt",
            )
        else:
            # Fallback text summary
            lines = [f"🧾 *{intent.caption or 'Receipt'}*"]
            for k, v in receipt_data.items():
                if v:
                    lines.append(f"*{k}:* {v}")
            await self.client.send_text(
                to=context.phone_number,
                text="\n".join(lines),
            )

    async def _present_flow(self, intent: ShowFlow, context: PresentationContext) -> None:
        supports_flows = context.capabilities.get("flows", False)
        if supports_flows and intent.flow_id:
            await self.client.send_flow(
                to=context.phone_number,
                flow_id=intent.flow_id,
                flow_config=intent.flow_config,
            )
        else:
            fallback = intent.fallback_text or "This action requires flow support on your channel."
            await self.client.send_text(to=context.phone_number, text=fallback)
