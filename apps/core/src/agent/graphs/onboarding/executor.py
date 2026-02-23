"""Onboarding handler"""

from typing import Any

from apps.core.src.agent.graphs.onboarding.service import OnboardingService
from shared.database.models import UserOnboardingStatusEnum
from shared.models.messages import ChannelMessage
from shared.models.user import UserCreate
from shared.repositories.unit_of_work import UnitOfWork
from shared.repositories.user_repository import UserRepository


class OnboardingExecutor:
    """Onboarding handler"""

    def __init__(
        self,
        user_repository: UserRepository,
        onboarding_service: OnboardingService,
    ):
        self.user_repository = user_repository
        self.onboarding_service = onboarding_service

    async def handle_onboarding(self, message: ChannelMessage) -> dict[str, Any]:
        """Handle onboarding messages - start flow."""
        channel_user_id = message.channel_user_id

        # When onboarding starts from Telegram via contact share, we inject onboarding_phone
        actual_phone = message.channel_metadata.get("onboarding_phone", channel_user_id)

        await self.onboarding_service.send_onboarding_flow(channel_user_id, channel=message.channel)

        async with UnitOfWork() as uow:
            # Check if user already exists
            existing_user = await uow.users.get_by_channel_identity(message.channel, channel_user_id)
            if not existing_user:
                user_data = UserCreate(
                    phone_number=actual_phone,
                    onboarding_status=UserOnboardingStatusEnum.ONBOARDING_STARTED.value,
                )
                created_user = await uow.users.register_user(user_data)
                await uow.users.link_channel_identity(created_user.id, message.channel, channel_user_id)

        return {"status": "success", "message": "Onboarding started"}
