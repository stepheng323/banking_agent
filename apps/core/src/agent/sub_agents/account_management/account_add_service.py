"""Account add service - re-exported from shared for backward compatibility.

DEPRECATED: Import from shared.services.onboarding instead:
    from shared.services.onboarding import AccountAddService, account_add_service
"""

# Re-export from shared location
from shared.services.onboarding import AccountAddService

__all__ = ["AccountAddService"]
