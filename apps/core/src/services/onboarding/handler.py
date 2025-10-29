from typing import Any, Dict

from apps.core.src.services.onboarding.onboarding_service import OnboardingService
from shared.clients.whatsapp_client import WhatsAppClient
from shared.database.models import UserOnboardingStatusEnum
from shared.models.messages import WhatsAppMessage
from shared.models.user import UserCreate
from shared.repositories.unit_of_work import UnitOfWork
from shared.repositories.user_repository import UserRepository


class OnboardingHandler:

    def __init__(
        self,
        whatsapp_client: WhatsAppClient,
        user_repository: UserRepository,
        onboarding_service: OnboardingService,
    ):
        self.whatsapp_client = whatsapp_client
        self.user_repository = user_repository
        self.onboarding_service = onboarding_service

    async def handle_onboarding(self, message: WhatsAppMessage) -> Dict[str, Any]:
        """Handle onboarding messages - start flow."""
        phone_number = message.from_number
        await self.onboarding_service.send_onboarding_flow(phone_number)
        with UnitOfWork() as uow:
            user_data = UserCreate(
                phone_number=phone_number,
                onboarding_status=UserOnboardingStatusEnum.ONBOARDING_STARTED.value,
            )
            uow.users.register_user(user_data)

        return None
