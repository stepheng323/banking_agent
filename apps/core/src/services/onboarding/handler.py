from shared.models.messages import WhatsAppMessage
from typing import Dict, Any
from shared.clients.whatsapp_client import WhatsAppClient

ONBOARDING_FLOW_ID = "1212187900453009"


class OnboardingHandler:

    def __init__(self, whatsapp_client: WhatsAppClient):
        self.whatsapp_client = whatsapp_client

    async def handle_onboarding(self, message: WhatsAppMessage) -> Dict[str, Any]:
        """Handle onboarding messages - start flow."""
        phone_number = message.from_number
        await self.send_onboarding_flow(phone_number)
        # TODO: Track onboarding start status in database
        return None

    async def send_onboarding_flow(self, phone_number: str) -> None:
        """Send the onboarding flow to the user."""
        try:
            await self.whatsapp_client.send_flow(
                to=phone_number,
                flow_cta="Start Onboarding",
                flow_id=ONBOARDING_FLOW_ID,
                screen_name="BVN_ENTRY",
                header="Welcome to Fusepay",
                flow_token=f"onboarding-flow-{phone_number}",
                text_body="Hi, I'm Fusepay an AI banking assistant that can help you with your banking needs. To get started, please complete the onboarding form below.",
            )
        except Exception as e:
            print(f"❌ Failed to send onboarding flow: {e}")
            raise e
