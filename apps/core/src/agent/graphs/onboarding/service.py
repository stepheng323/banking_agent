from shared.clients.whatsapp.client import WhatsAppClient
from shared.config import settings
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class OnboardingService:
    def __init__(self, whatsapp_client: WhatsAppClient):
        self.whatsapp_client = whatsapp_client

    async def send_onboarding_flow(self, phone_number: str) -> None:
        """Send the onboarding flow to the user."""
        try:
            await self.whatsapp_client.send_flow(
                to=phone_number,
                flow_cta="Start Onboarding",
                flow_id=settings.onboarding_flow_id,
                screen_name="BVN_ENTRY",
                header="Welcome to Fusepay",
                flow_token=f"onboarding-flow-{phone_number}",
                text_body="Hi, I'm Fusepay an AI banking assistant that can help you with your banking needs. To get started, please complete the onboarding form below.",
            )
        except Exception as e:
            logger.error("failed_to_send_onboarding")
            raise e
