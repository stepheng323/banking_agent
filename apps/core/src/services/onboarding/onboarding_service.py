import asyncio
from typing import Any, Dict
from shared.models.messages import WhatsAppMessage


class OnboardingService:
    def __init__(self):
        pass

    def _get_state(self, user_id: str) -> str:
        conv_state = self.user_registry.get_conversation_state(user_id)
        return conv_state.current_state if conv_state else "start"

    def _save_state(self, user_id: str, state: str, state_data: dict = None):
        self.user_registry.save_conversation_state(user_id, state, state_data or {})

    async def handle_welcome(self, message: WhatsAppMessage) -> Dict[str, Any]:
        response = {
            "intent": "onboarding_welcome",
            "text": "👋 Welcome to Banking Agent! Let's set up your account.\n\nPlease reply with your full name:",
            "actions": ["collect_name"],
        }
        await asyncio.sleep(0.1)
        return response

    async def _handle_collect_name(self, message: WhatsAppMessage) -> Dict[str, Any]:
        name = message.text.strip()
        response = {
            "intent": "onboarding_name_collected",
            "text": f"Great, {name}! Now please provide your email address:",
            "actions": ["collect_email"],
            "entities": {"name": name},
        }
        await asyncio.sleep(0.1)
        return response

    async def _handle_collect_email(self, message: WhatsAppMessage) -> Dict[str, Any]:
        email = message.text.strip()
        response = {
            "intent": "onboarding_complete",
            "text": f"✅ Registration complete!\n\nEmail: {email}\n\nYou can now use banking services. Try 'check balance' or 'help'.",
            "actions": ["registration_complete"],
            "entities": {"email": email},
        }
        await asyncio.sleep(0.1)
        return response

    def reset_conversation(self, user_id: str):
        if user_id in self.conversation_state:
            del self.conversation_state[user_id]
