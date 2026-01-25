from apps.core.src.agent.orchestrator.models.intents import ShowFlow
from apps.core.src.messaging.outbox import enqueue_outbox_intents
from shared.config import settings
from shared.queue.redis_queue import RedisQueue
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class OnboardingService:
    def __init__(self, queue: RedisQueue):
        self.queue = queue

    async def send_onboarding_flow(self, phone_number: str, channel: str = "whatsapp") -> None:
        """Send the onboarding flow to the user."""
        try:
            fallback_text = (
                "Welcome to Fusepay! To complete your onboarding, please visit our secure portal: "
                "https://fusepay.io/onboard\n\n(Interactive onboarding is not supported on this channel)"
            )
            intent = ShowFlow(
                flow_id=settings.onboarding_flow_id,
                flow_config={
                    "flow_cta": "Start Onboarding",
                    "screen_name": "BVN_ENTRY",
                    "header": "Welcome to Fusepay",
                    "flow_token": f"onboarding-flow-{phone_number}",
                    "text_body": "Hi, I'm Fusepay an AI banking assistant that can help you with your banking needs. To get started, please complete the onboarding form below.",
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
