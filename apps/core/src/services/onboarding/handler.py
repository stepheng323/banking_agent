from apps.core.src.services.onboarding.onboarding_service import OnboardingService
from shared.models.messages import WhatsAppMessage
from typing import Dict, Any
from shared.clients.whatsapp_client import WhatsAppClient


ONBOARDING_FLOW_ID = "1212187900453009"


class OnboardingHandler:

    def __init__(self, user_registry):
        self.user_registry = user_registry
        self.onboarding_service = OnboardingService()
        self.whatsapp_client = WhatsAppClient()

    async def handle_message(self, message: WhatsAppMessage) -> Dict[str, Any]:
        """Handle onboarding message - start the interactive flow."""
        user_id = message.from_number
        text = message.text.strip().lower() if message.text else ""

        try:
            await self.whatsapp_client.send_flow(
                to=user_id,
                flow_cta="Start Onboarding",
                flow_id=ONBOARDING_FLOW_ID,
                screen_name="RECOMMEND",
                header="Welcome to Fusepay",
                text_body="Hi, I'm Fusepay an AI banking assistant that can help you with your banking needs. To get started, please complete the onboarding form below.",
            )
        except Exception as e:
            print(f"❌ Failed to send onboarding flow: {e}")
            raise e

        return None
