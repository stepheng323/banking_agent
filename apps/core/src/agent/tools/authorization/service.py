"""Authorization service - re-exported from shared for backward compatibility.

DEPRECATED: Import from shared.services.auth instead:
    from shared.services.auth import AuthorizationService, AuthorizationResult
"""

# Re-export from shared location
from shared.services.auth import AuthorizationService, AuthorizationResult

__all__ = ["AuthorizationService", "AuthorizationResult"]
