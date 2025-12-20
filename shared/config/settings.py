"""Shared application configuration."""

import os
from pathlib import Path
from dotenv import load_dotenv


_env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(dotenv_path=_env_path, override=True)


class Settings:
    """Application settings loaded from environment variables and .env file."""

    def __init__(self):
        self.meta_verify_token: str = os.getenv(
            "META_VERIFY_TOKEN", "development_token")
        self.meta_access_token: str = os.getenv(
            "META_ACCESS_TOKEN", "development_access_token")
        self.meta_phone_number_id: str = os.getenv(
            "META_PHONE_NUMBER_ID", "development_phone_id")
        self.whatsapp_flow_private_key_path: str = os.getenv(
            "WHATSAPP_FLOW_PRIVATE_KEY_PATH", "")
        self.onboarding_flow_id: str = os.getenv(
            "ONBOARDING_FLOW_ID", "")

        self.pin_confirmation_flow_id: str = os.getenv(
            "PIN_CONFIRMATION_FLOW_ID", "")

        self.app_env: str = os.getenv("APP_ENV", "development")
        self.app_host: str = os.getenv("APP_HOST", "0.0.0.0")
        self.app_port: int = int(os.getenv("APP_PORT", "8000"))

        self.database_url: str = os.getenv(
            "DATABASE_URL", "sqlite:///./test.db")

        self.redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379")

        self.user_ctx_ttl_seconds: int = int(
            os.getenv("TTL_SECONDS", "6000"))

        self.flow_session_timeout: int = int(
            os.getenv("FLOW_SESSION_TIMEOUT", "600"))

        self.pending_transaction_ttl: int = int(
            os.getenv("PENDING_TRANSACTION_TTL", "900"))

        self.openai_api_key: str = os.getenv("OPENAI_API_KEY", "")

        self.flutterwave_secret_key: str = os.getenv(
            "FLUTTERWAVE_SECRET_KEY", "")
        self.flutterwave_use_sandbox: bool = os.getenv(
            "FLUTTERWAVE_USE_SANDBOX", "false").lower() == "true"

        self.mono_api_key: str = os.getenv("MONO_API_KEY", "")

        self.s3_bucket_name: str = os.getenv("S3_BUCKET_NAME", "")
        self.s3_region: str = os.getenv("AWS_REGION", "us-east-1")
        self.s3_receipt_prefix: str = "receipts"

        self._validate_whatsapp_config()

    def _validate_whatsapp_config(self) -> None:
        """Validate WhatsApp configuration and warn about missing values."""
        warnings = []

        if self.meta_access_token == "development_access_token":
            warnings.append(
                "META_ACCESS_TOKEN is not set - using development default")

        if self.meta_phone_number_id == "development_phone_id":
            warnings.append(
                "META_PHONE_NUMBER_ID is not set - using development default")

        if not self.onboarding_flow_id:
            warnings.append(
                "ONBOARDING_FLOW_ID is using default - update with your actual Flow ID")

        if warnings and self.app_env != "dev":
            print("⚠️  Configuration warnings:")
            for warning in warnings:
                print(f"   - {warning}")
            print(
                "   Create a .env file with proper WhatsApp credentials to fix these warnings.")


settings = Settings()
