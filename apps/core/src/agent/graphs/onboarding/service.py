from apps.core.src.agent.orchestrator.models.intents import ShowFlow
from apps.core.src.messaging.outbox import enqueue_outbox_intents
from shared.config import settings
from shared.i18n import render_message
from shared.queue.redis_queue import RedisQueue
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class OnboardingService:
    def __init__(self, queue: RedisQueue):
        self.queue = queue

    async def send_onboarding_flow(self, phone_number: str, channel: str = "whatsapp", locale: str = "en") -> None:
        """Send the onboarding flow to the user."""
        try:
            onboarding_url = "https://fusepay.io/onboard"
            fallback_text = render_message(
                "onboarding.fallback_text",
                locale,
                {"onboarding_url": onboarding_url},
            )
            intent = ShowFlow(
                flow_id=settings.onboarding_flow_id,
                flow_config={
                    "flow_cta": render_message("onboarding.flow.cta", locale),
                    "screen_name": "BVN_ENTRY",
                    "header": render_message("onboarding.flow.header", locale),
                    "flow_token": f"onboarding-flow-{phone_number}",
                    "text_body": render_message("onboarding.flow.body", locale),
                },
                fallback_text=fallback_text,
            )
            await enqueue_outbox_intents(
                self.queue,
                phone_number,
                channel,
                [intent],
                metadata={"source": "onboarding"},
            )
        except Exception as e:
            logger.error("failed_to_send_onboarding")
            raise e
