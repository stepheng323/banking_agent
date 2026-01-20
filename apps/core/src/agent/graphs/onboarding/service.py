from shared.clients.abstractions.messaging import MessagingClient
from shared.config import settings
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class OnboardingService:
    def __init__(self, messaging_client: MessagingClient):
        self.messaging_client = messaging_client

    async def send_onboarding_flow(self, phone_number: str) -> None:
        """Send the onboarding flow to the user."""
        try:
            if self.messaging_client.supports_flows:
                await self.messaging_client.send_flow(
                    to=phone_number,
                    flow_id=settings.onboarding_flow_id,
                    flow_config={
                        "flow_cta": "Start Onboarding",
                        "screen_name": "BVN_ENTRY",
                        "header": "Welcome to Fusepay",
                        "flow_token": f"onboarding-flow-{phone_number}",
                        "text_body": "Hi, I'm Fusepay an AI banking assistant that can help you with your banking needs. To get started, please complete the onboarding form below.",
                    },
                )
            else:
                await self.messaging_client.send_text(
                    to=phone_number,
                    text=(
                        "Welcome to Fusepay! To complete your onboarding, please visit our secure portal: "
                        "https://fusepay.io/onboard\n\n(Interactive onboarding is not supported on this channel)"
                    ),
                )
        except Exception as e:
            logger.error("failed_to_send_onboarding")
            raise e
