from shared.models.messages import WhatsAppMessage
from apps.core.src.services.onboarding.handler import OnboardingHandler
from apps.core.src.services.user_registry import UserRegistry
from typing import Dict, Any


class MessageHandler:
    def __init__(self):
        self.user_registry = UserRegistry()
        self.onboarding_handler = OnboardingHandler(self.user_registry)

    async def handle_message(self, message: WhatsAppMessage) -> Dict[str, Any]:
        user_id = message.from_number
        
        if not self.user_registry.is_registered(user_id):
            return await self.onboarding_handler.handle_message(message)
            
          
