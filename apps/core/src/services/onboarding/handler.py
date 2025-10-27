from shared.database.models import UserOnboardingStatusEnum
from shared.models.messages import WhatsAppMessage
from typing import Dict, Any
from shared.clients.whatsapp_client import WhatsAppClient
from shared.models.user import UserCreate
from shared.repositories.user_repository import UserRepository

ONBOARDING_FLOW_ID = "1212187900453009"


class OnboardingHandler:

    def __init__(
        self, whatsapp_client: WhatsAppClient, user_repository: UserRepository
    ):
        self.whatsapp_client = whatsapp_client
        self.user_repository = user_repository

    async def handle_onboarding(self, message: WhatsAppMessage) -> Dict[str, Any]:
        """Handle onboarding messages - start flow."""
        phone_number = message.from_number
        await self.send_onboarding_flow(phone_number)
        
        # Track onboarding start - use UnitOfWork to ensure commit
        from shared.repositories.unit_of_work import UnitOfWork
        with UnitOfWork() as uow:
            user_data = UserCreate(
                phone_number=phone_number,
                onboarding_status=UserOnboardingStatusEnum.ONBOARDING_STARTED.value,
            )
            uow.users.register_user(user_data)
        
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
