"""Service context for airtime flow nodes."""

from typing import Optional

from apps.core.src.agent.sub_agents.airtime.extractor import AirtimeEntityExtractor
from apps.core.src.agent.tools.beneficiary.matcher import BeneficiaryMatcher
from shared.cache.redis_client import Redis
from shared.cache.user_data import UserDataCache
from shared.clients.whatsapp.client import WhatsAppClient
from shared.queue.redis_queue import RedisQueue
from shared.repositories import AccountRepository, BeneficiaryRepository
from shared.services.auth import AuthorizationService


class AirtimeNodeContext:
    """Service context for airtime flow nodes.

    This allows nodes to access dependencies without requiring them as parameters,
    making all nodes have the signature: (state: AirtimeState) -> AirtimeState
    """

    _instance: Optional["AirtimeNodeContext"] = None

    def __init__(self):
        self.extractor: AirtimeEntityExtractor | None = None
        self.user_cache: UserDataCache | None = None
        self.account_repo: AccountRepository | None = None
        self.beneficiary_repo: BeneficiaryRepository | None = None
        self.whatsapp_client: WhatsAppClient | None = None
        self.redis_client: Redis | None = None
        self.queue: RedisQueue | None = None
        self.matcher: BeneficiaryMatcher | None = None
        self._authorization_service: AuthorizationService | None = None

    @classmethod
    def get(cls) -> "AirtimeNodeContext":
        """Get the singleton instance of the context."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton instance (useful for testing)."""
        cls._instance = None

    @property
    def authorization_service(self) -> AuthorizationService:
        """Get or create the authorization service."""
        if self._authorization_service is None:
            if self.redis_client is None:
                raise ValueError("redis_client must be set before accessing authorization_service")
            self._authorization_service = AuthorizationService(redis_client=self.redis_client)
        return self._authorization_service

    def setup(
        self,
        extractor: AirtimeEntityExtractor,
        user_cache: UserDataCache,
        account_repo: AccountRepository,
        beneficiary_repo: BeneficiaryRepository,
        whatsapp_client: WhatsAppClient,
        redis_client: Redis,
        queue: RedisQueue,
        matcher: BeneficiaryMatcher,
    ) -> None:
        """Set up all dependencies in the context."""
        self.extractor = extractor
        self.user_cache = user_cache
        self.account_repo = account_repo
        self.beneficiary_repo = beneficiary_repo
        self.whatsapp_client = whatsapp_client
        self.redis_client = redis_client
        self.queue = queue
        self.matcher = matcher
