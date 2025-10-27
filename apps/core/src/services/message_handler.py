from shared.models.messages import WhatsAppMessage
from apps.core.src.services.onboarding.handler import OnboardingHandler
from shared.clients.whatsapp_client import WhatsAppClient
from typing import Dict, Any

from shared.repositories.user_repository import UserRepository


class MessageHandler:
    def __init__(
        self,
        whatsapp_client: WhatsAppClient,
        user_repository: UserRepository,
    ):
        self.whatsapp_client = whatsapp_client
        self.user_repository = user_repository
        self.onboarding_handler = OnboardingHandler(whatsapp_client, user_repository)

    async def handle_message(self, message: WhatsAppMessage) -> Dict[str, Any]:
        phone_number = message.from_number
        user = self.user_repository.get_by_phone(phone_number)
        if not user:
            return await self.onboarding_handler.handle_onboarding(message)
