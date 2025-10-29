from typing import Any, Dict

from apps.core.src.services.onboarding.handler import OnboardingHandler
from shared.clients.whatsapp_client import WhatsAppClient
from shared.database.models import UserOnboardingStatusEnum
from shared.models.messages import WhatsAppMessage
from shared.repositories.user_repository import UserRepository


class MessageHandler:
    def __init__(
        self,
        whatsapp_client: WhatsAppClient,
        user_repository: UserRepository,
        onboarding_handler: OnboardingHandler,
    ):
        self.whatsapp_client = whatsapp_client
        self.user_repository = user_repository
        self.onboarding_handler = onboarding_handler

    async def handle_message(self, message: WhatsAppMessage) -> Dict[str, Any]:
        phone_number = message.from_number
        user = self.user_repository.get_by_phone(phone_number)

        if user is None or user.onboarding_status != UserOnboardingStatusEnum.ONBOARDING_COMPLETED:
            return await self.onboarding_handler.handle_onboarding(message)

        # TODO: Implement banking message handling
        return {"status": "banking_operations_not_implemented"}
