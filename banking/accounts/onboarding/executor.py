"""Onboarding handler"""

from typing import Any

from langchain_openai import ChatOpenAI

from banking.accounts.onboarding.pre_onboarding_classifier import PreOnboardingClassifier
from banking.accounts.onboarding.pre_onboarding_gate import PreOnboardingGate
from banking.accounts.onboarding.service import OnboardingService
from banking.identity.repositories.user_repository import UserRepository
from banking.persistence.unit_of_work import UnitOfWork
from banking.presentation.i18n.renderer import render_message
from shared.config.settings import settings
from shared.database.models import UserOnboardingStatusEnum
from shared.messaging.intents import Say
from shared.messaging.outbox import enqueue_outbox_intents
from shared.models.messages import ChannelMessage
from shared.models.user import UserCreate


class OnboardingExecutor:
    """Onboarding handler"""

    def __init__(
        self,
        user_repository: UserRepository,
        onboarding_service: OnboardingService,
        pre_onboarding_gate: PreOnboardingGate | None = None,
    ):
        self.user_repository = user_repository
        self.onboarding_service = onboarding_service
        self.pre_onboarding_gate: PreOnboardingGate = pre_onboarding_gate or PreOnboardingGate(
            classifier=PreOnboardingClassifier(llm=ChatOpenAI(model=settings.semantic_router_model, temperature=0.0))
        )

    async def handle_onboarding(self, message: ChannelMessage) -> dict[str, Any]:
        """Handle onboarding messages - start flow."""
        channel_user_id = message.channel_user_id

        actual_phone = message.channel_metadata.get("onboarding_phone", channel_user_id)

        response = await self.pre_onboarding_gate.handle_unonboarded_message(
            channel=message.channel,
            channel_user_id=channel_user_id,
            text=message.text or "",
        )

        lang = response.lang or "en"
        locale = "en"
        lang_lower = lang.lower()
        if "pidgin" in lang_lower:
            locale = "pcm"
        elif "yoruba" in lang_lower:
            locale = "yo"
        elif "hausa" in lang_lower:
            locale = "ha"
        elif "igbo" in lang_lower:
            locale = "ig"

        if response.should_resend_onboarding_cta:
            message_text = render_message(
                response.message_key,
                locale,
                params={"app_name": settings.app_name},
            )
            await self.onboarding_service.send_onboarding_flow(
                channel_user_id,
                channel=message.channel,
                locale=locale,
                onboarding_phone=actual_phone,
                override_body=message_text,
            )
        else:
            message_text = render_message(
                response.message_key,
                locale,
                params={"app_name": settings.app_name},
            )

            intent = Say(text=message_text)
            await enqueue_outbox_intents(
                self.onboarding_service.publisher,
                channel_user_id,
                message.channel,
                [intent],
                metadata={"source": "onboarding_gate"},
            )

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

        return {"status": "success", "message": "Onboarding processed", "action": response.action}
