"""Shared application configuration."""

import os


class Settings:
    """Application settings loaded from environment variables and .env file."""

    def __init__(self):
        # WhatsApp/Meta settings
        self.meta_verify_token: str = os.getenv("META_VERIFY_TOKEN", "development_token")
        self.meta_access_token: str = os.getenv("META_ACCESS_TOKEN", "development_access_token")
        self.meta_phone_number_id: str = os.getenv("META_PHONE_NUMBER_ID", "development_phone_id")
        self.whatsapp_flow_private_key_path: str = os.getenv("WHATSAPP_FLOW_PRIVATE_KEY_PATH", "")
        self.onboarding_flow_id: str = os.getenv("ONBOARDING_FLOW_ID", "1212187900453009")

        # Application settings
        self.app_env: str = os.getenv("APP_ENV", "dev")
        self.app_host: str = os.getenv("APP_HOST", "0.0.0.0")
        self.app_port: int = int(os.getenv("APP_PORT", "8000"))

        # Database settings
        self.database_url: str = os.getenv("DATABASE_URL", "sqlite:///./test.db")

        # Redis settings
        self.redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379")

        # Validate critical settings
        self._validate_whatsapp_config()

    def _validate_whatsapp_config(self) -> None:
        """Validate WhatsApp configuration and warn about missing values."""
        warnings = []

        if self.meta_access_token == "development_access_token":
            warnings.append("META_ACCESS_TOKEN is not set - using development default")

        if self.meta_phone_number_id == "development_phone_id":
            warnings.append("META_PHONE_NUMBER_ID is not set - using development default")

        if not self.onboarding_flow_id or self.onboarding_flow_id == "1212187900453009":
            warnings.append("ONBOARDING_FLOW_ID is using default - update with your actual Flow ID")

        if warnings and self.app_env != "dev":
            print("⚠️  Configuration warnings:")
            for warning in warnings:
                print(f"   - {warning}")
            print("   Create a .env file with proper WhatsApp credentials to fix these warnings.")


# Global settings instance
settings = Settings()
