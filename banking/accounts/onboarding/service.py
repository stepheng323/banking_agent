import secrets

from banking.accounts.onboarding.runtime import session_manager as default_session_manager
from banking.accounts.onboarding.session import OnboardingStep
from banking.presentation.i18n.renderer import render_message
from shared.cache.flow_session_manager import FlowSessionManager
from shared.config.settings import settings
from shared.messaging.intents import ShowFlow
from shared.messaging.outbox import enqueue_outbox_intents
from shared.queue.adapter import QueuePublisher
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class OnboardingService:
    def __init__(self, publisher: QueuePublisher, session_manager: FlowSessionManager | None = None):
        self.publisher = publisher
        self.session_manager = session_manager or default_session_manager

    @staticmethod
    def _new_flow_token() -> str:
        return f"onboarding-{secrets.token_urlsafe(32)}"

    async def send_onboarding_flow(
        self,
        phone_number: str,
        channel: str = "whatsapp",
        locale: str = "en",
        onboarding_phone: str | None = None,
        override_body: str | None = None,
    ) -> None:
        """Send the onboarding flow to the user."""
        try:
            flow_token = self._new_flow_token()
            session_phone = (onboarding_phone or phone_number or "").strip()
            stored = await self.session_manager.update_session_strict(
                flow_token,
                {
                    "phone_number": session_phone,
                    "channel": channel,
                    "channel_user_id": phone_number,
                    "step": OnboardingStep.BVN_ENTRY.value,
                },
                verify=True,
            )
            if not stored:
                raise RuntimeError("Failed to create onboarding session")

            onboarding_url = f"{settings.app_public_base_url}/onboard"
            fallback_text = render_message(
                "onboarding.fallback_text",
                locale,
                {"onboarding_url": onboarding_url},
            )
            intent = ShowFlow(
                flow_id=settings.whatsapp.onboarding_flow_id,
                flow_config={
                    "flow_cta": render_message("onboarding.flow.cta", locale),
                    "screen_name": "BVN_ENTRY",
                    "header": render_message("onboarding.flow.header", locale),
                    "flow_token": flow_token,
                    "text_body": override_body or render_message("onboarding.flow.body", locale),
                },
                fallback_text=fallback_text,
            )
            await enqueue_outbox_intents(
                self.publisher,
                phone_number,
                channel,
                [intent],
                metadata={"source": "onboarding"},
            )
        except Exception as e:
            logger.error("failed_to_send_onboarding")
            raise e
