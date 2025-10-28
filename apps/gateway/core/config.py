"""Gateway app configuration - imports from shared config."""

# Import shared settings to maintain backward compatibility
from shared.config import Settings, settings

__all__ = ["Settings", "settings"]
