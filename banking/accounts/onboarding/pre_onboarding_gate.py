"""Pre-onboarding gate to manage unonboarded user interactions and enforce caps."""

from dataclasses import dataclass
from typing import Any, Literal

from banking.accounts.onboarding.pre_onboarding_classifier import (
    PreOnboardingCategory,
    PreOnboardingClassifier,
    SupportedLanguage,
)
from banking.presentation.i18n.message_keys import MessageKey
from shared.cache.redis_client import RedisClient
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_INTERACTION_CAP = 5
_COOLDOWN_TTL_SECONDS = 3600


@dataclass(slots=True)
class PreOnboardingResponse:
    """The result of the pre-onboarding gate check."""

    action: Literal["respond", "cooldown"]
    message_key: MessageKey
    category: PreOnboardingCategory | None = None
    lang: SupportedLanguage | None = None
    should_resend_onboarding_cta: bool = False


_CATEGORY_MAPPING: dict[PreOnboardingCategory, tuple[MessageKey, bool]] = {
    PreOnboardingCategory.GREETING: ("pre_onboarding.greeting", True),
    PreOnboardingCategory.ONBOARDING_INQUIRY: ("pre_onboarding.inquiry", True),
    PreOnboardingCategory.ONBOARDING_COMPLIANCE: ("pre_onboarding.compliance", True),
    PreOnboardingCategory.ONBOARDING_OBJECTION: ("pre_onboarding.objection", True),
    PreOnboardingCategory.BANKING_REQUEST: ("pre_onboarding.banking_request", True),
    PreOnboardingCategory.OUT_OF_SCOPE: ("pre_onboarding.out_of_scope", False),
}


class PreOnboardingGate:
    """Manages pre-onboarding classification and interaction limits."""

    def __init__(self, classifier: PreOnboardingClassifier, redis: Any = None) -> None:
        self.classifier = classifier
        self.redis = redis or RedisClient.get_client()

    def _get_counter_key(self, channel: str, channel_user_id: str) -> str:
        return f"pre_onboarding:{channel}:{channel_user_id}:count"

    async def _increment_and_check_cap(self, channel: str, channel_user_id: str) -> bool:
        """
        Increments the interaction counter and returns True if the user is under the cap.
        Returns False if the user has hit or exceeded the cap.
        """
        key = self._get_counter_key(channel, channel_user_id)
        try:
            count = await self.redis.incr(key)
            if count == 1:
                await self.redis.expire(key, _COOLDOWN_TTL_SECONDS)

            logger.info("pre_onboarding_interaction", channel=channel, count=count)
            return int(count) <= _INTERACTION_CAP
        except Exception as exc:
            logger.warning("pre_onboarding_cap_check_failed", error=str(exc))
            return True

    async def handle_unonboarded_message(
        self,
        channel: str,
        channel_user_id: str,
        text: str,
    ) -> PreOnboardingResponse:
        """
        Process a message from an unonboarded user.
        Enforces the interaction cap and returns the appropriate classified response.
        """
        under_cap = await self._increment_and_check_cap(channel, channel_user_id)
        if not under_cap:
            logger.info("pre_onboarding_cooldown_triggered", channel=channel)
            return PreOnboardingResponse(
                action="cooldown",
                message_key="pre_onboarding.cooldown",
                should_resend_onboarding_cta=False,
            )

        decision = await self.classifier.classify(text, phone_number=channel_user_id)

        message_key, should_resend_cta = _CATEGORY_MAPPING.get(
            decision.category,
            ("pre_onboarding.out_of_scope", False),
        )

        return PreOnboardingResponse(
            action="respond",
            category=decision.category,
            message_key=message_key,
            lang=decision.lang,
            should_resend_onboarding_cta=should_resend_cta,
        )
