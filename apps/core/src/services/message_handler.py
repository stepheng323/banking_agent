from shared.models.messages import WhatsAppMessage
from apps.core.src.services.onboarding.handler import OnboardingHandler
from shared.clients.whatsapp_client import WhatsAppClient
from typing import Dict, Any


class MessageHandler:
    def __init__(
        self,
        whatsapp_client: WhatsAppClient,
    ):
        self.whatsapp_client = whatsapp_client
        self.onboarding_handler = OnboardingHandler(whatsapp_client)

    async def handle_message(self, message: WhatsAppMessage) -> Dict[str, Any]:
        user_id = message.from_number
        # TODO: Check if user is registered
        return await self.onboarding_handler.handle_onboarding(message)
