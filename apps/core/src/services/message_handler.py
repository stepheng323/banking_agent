"""Handle incomming Messages"""
from typing import Any, Dict

from apps.core.src.services.onboarding.handler import OnboardingHandler
from apps.core.src.agent.orchestrator import OrchestratorAgent
from shared.clients.whatsapp_client import WhatsAppClient
from shared.database.models import UserOnboardingStatusEnum
from shared.models.messages import WhatsAppMessage
from shared.repositories.user_repository import UserRepository


class MessageHandler:
    """Message Hanlder Class"""

    def __init__(
        self,
        whatsapp_client: WhatsAppClient,
        user_repository: UserRepository,
        onboarding_handler: OnboardingHandler,
        orchestrator: OrchestratorAgent,
    ):
        self.whatsapp_client = whatsapp_client
        self.user_repository = user_repository
        self.onboarding_handler = onboarding_handler
        self.orchestrator = orchestrator

    async def handle_message(self, message: WhatsAppMessage) -> Dict[str, Any]:
        """Handle a WhatsApp message."""
        phone_number = message.from_number
        user = self.user_repository.get_by_phone(phone_number)

        if user is None or getattr(user, "onboarding_status", None) != UserOnboardingStatusEnum.ONBOARDING_COMPLETED:
            return await self.onboarding_handler.handle_onboarding(message)

        response = await self.orchestrator.invoke(
            phone_number, message.text or "", message.message_id
        )
        # Only send text message if response is not empty
        # (Some flows like transfer confirmation send interactive flows instead)
        if response and response.strip():
            await self.whatsapp_client.send_text(phone_number, response)

        return {"status": "success", "response": response}
