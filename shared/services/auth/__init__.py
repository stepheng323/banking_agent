"""Authorization services package."""

from shared.cache.redis_client import RedisClient

from .authorization import AuthorizationResult, AuthorizationService

__all__ = [
    "AuthorizationService",
    "AuthorizationResult",
    "authorization_service",
]

# Singleton instance for easy import
_authorization_service = None


def get_authorization_service() -> AuthorizationService:
    """Get or create the authorization service singleton."""
    global _authorization_service
    if _authorization_service is None:
        _authorization_service = AuthorizationService(redis_client=RedisClient.get_client())
    return _authorization_service


authorization_service = get_authorization_service()
