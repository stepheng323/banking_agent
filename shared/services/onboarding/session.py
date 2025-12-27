"""Session management for onboarding flow."""

import json
from dataclasses import dataclass
from enum import Enum

from shared.cache.redis_client import RedisClient
from shared.utils.logging import get_logger

logger = get_logger(__name__)

SESSION_TTL = 3600  # 1 hour


class OnboardingStep(str, Enum):
    BVN_ENTRY = "bvn_entry"
    METHOD_SELECTION = "method_selection"
    OTP_VERIFICATION = "otp_verification"
    ACCOUNT_SELECTION = "account_selection"
    PIN_ENTRY = "pin_entry"
    COMPLETE = "complete"


@dataclass
class OnboardingSession:
    """Onboarding session state."""

    phone_number: str
    bvn: str | None = None
    session_id: str | None = None
    methods: list[dict] | None = None
    selected_method: str | None = None
    otp_verified: bool = False
    accounts: list[dict] | None = None
    selected_account: str | None = None
    email: str | None = None
    address: str | None = None
    step: OnboardingStep = OnboardingStep.BVN_ENTRY
    is_account_linking: bool = False


class SessionManager:
    """Manages onboarding session state in Redis."""

    def __init__(self, redis: RedisClient | None = None):
        self.redis = redis or RedisClient.get_client()

    def _session_key(self, flow_token: str) -> str:
        return f"onboarding:{flow_token}"

    async def get_session(self, flow_token: str) -> OnboardingSession | None:
        """Get onboarding session from Redis."""
        try:
            data = await self.redis.get(self._session_key(flow_token))
            if data:
                parsed = json.loads(data)
                return OnboardingSession(**parsed)
        except Exception as e:
            logger.error("get_session_error", error=str(e))
        return None

    async def get_session_data(self, flow_token: str) -> dict:
        """Get raw session data from Redis."""
        try:
            data = await self.redis.get(self._session_key(flow_token))
            if data:
                return json.loads(data)
        except Exception as e:
            logger.error("get_session_error", error=str(e))
        return {}

    async def update_session(self, flow_token: str, updates: dict) -> None:
        """Merge updates into existing session."""
        try:
            existing = await self.get_session_data(flow_token)
            existing.update(updates)
            await self.redis.set(
                self._session_key(flow_token), json.dumps(existing), ex=SESSION_TTL
            )
        except Exception as e:
            logger.error("update_session_error", error=str(e))

    async def delete_session(self, flow_token: str) -> None:
        """Delete session from Redis."""
        try:
            await self.redis.delete(self._session_key(flow_token))
        except Exception as e:
            logger.error("delete_session_error", error=str(e))
