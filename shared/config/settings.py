"""Shared application configuration."""

import os
from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(dotenv_path=_env_path, override=True)


class Settings:
    """Application settings loaded from environment variables and .env file."""

    def __init__(self) -> None:
        self.meta_verify_token: str = os.getenv("META_VERIFY_TOKEN", "development_token")
        self.meta_access_token: str = os.getenv("META_ACCESS_TOKEN", "development_access_token")
        self.meta_phone_number_id: str = os.getenv("META_PHONE_NUMBER_ID", "development_phone_id")
        self.whatsapp_flow_private_key_path: str = os.getenv("WHATSAPP_FLOW_PRIVATE_KEY_PATH", "")
        self.onboarding_flow_id: str = os.getenv("ONBOARDING_FLOW_ID", "")
        self.account_linking_flow_id: str = os.getenv("ACCOUNT_LINKING_FLOW_ID", "")

        self.pin_confirmation_flow_id: str = os.getenv("PIN_CONFIRMATION_FLOW_ID", "")

        self.app_env: str = os.getenv("APP_ENV", "development")
        self.project_name: str = os.getenv("PROJECT_NAME", "banking-agent")
        self.environment: str = os.getenv("ENVIRONMENT", "dev")
        self.app_host: str = os.getenv("APP_HOST", "0.0.0.0")
        self.app_port: int = int(os.getenv("APP_PORT", "8000"))

        self.database_url: str = os.getenv("DATABASE_URL", "sqlite:///./test.db")

        self.redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379")

        self.user_ctx_ttl_seconds: int = int(os.getenv("TTL_SECONDS", "6000"))

        self.flow_session_timeout: int = int(os.getenv("FLOW_SESSION_TIMEOUT", "600"))

        self.pending_transaction_ttl: int = int(os.getenv("PENDING_TRANSACTION_TTL", "300"))

        self.openai_api_key: str = os.getenv("OPENAI_API_KEY", "")

        self.flutterwave_secret_key: str = os.getenv("FLUTTERWAVE_SECRET_KEY", "")
        self.flutterwave_use_sandbox: bool = os.getenv("FLUTTERWAVE_USE_SANDBOX", "false").lower() == "true"

        self.mono_api_key: str = os.getenv("MONO_API_KEY", "")

        self.s3_bucket_name: str = os.getenv("S3_BUCKET_NAME", "")
        self.aws_region: str = os.getenv("AWS_REGION", "us-east-1")
        self.aws_account_id: str = os.getenv("AWS_ACCOUNT_ID", "000000000000")
        self.s3_region: str = self.aws_region
        self.s3_receipt_prefix: str = "receipts"

        self.default_channel: str = os.getenv("DEFAULT_CHANNEL", "whatsapp")

        self.telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_mini_app_base_url: str = os.getenv("TELEGRAM_MINI_APP_BASE_URL", "")
        self.telegram_webhook_secret_token: str = os.getenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "")

        self.soul_policy_path: str = os.getenv("SOUL_POLICY_PATH", "config/soul_policy.json")
        self.enable_channel_option_ux_v2: bool = os.getenv("ENABLE_CHANNEL_OPTION_UX_V2", "false").lower() == "true"

        self._validate_critical_runtime_config()
        self._validate_whatsapp_config()

    def _validate_whatsapp_config(self) -> None:
        """Validate WhatsApp configuration and warn about missing values."""
        warnings = []

        if self.meta_access_token == "development_access_token":
            warnings.append("META_ACCESS_TOKEN is not set - using development default")

        if self.meta_phone_number_id == "development_phone_id":
            warnings.append("META_PHONE_NUMBER_ID is not set - using development default")

        if not self.onboarding_flow_id:
            warnings.append("ONBOARDING_FLOW_ID is using default - update with your actual Flow ID")

        if warnings and self.app_env != "dev":
            print("⚠️  Configuration warnings:")
            for warning in warnings:
                print(f"   - {warning}")
            print("   Create a .env file with proper WhatsApp credentials to fix these warnings.")

    def _validate_critical_runtime_config(self) -> None:
        """Fail fast outside dev/test when critical runtime config is missing."""
        non_dev_env = self.app_env.lower() not in {"dev", "development", "test", "local"}
        if not non_dev_env:
            return

        missing: list[str] = []

        checks = {
            "DATABASE_URL": self.database_url and self.database_url != "sqlite:///./test.db",
            "REDIS_URL": self.redis_url and self.redis_url != "redis://localhost:6379",
            "OPENAI_API_KEY": bool(self.openai_api_key),
            "MONO_API_KEY": bool(self.mono_api_key),
            "FLUTTERWAVE_SECRET_KEY": bool(self.flutterwave_secret_key),
            "META_ACCESS_TOKEN": self.meta_access_token != "development_access_token",
            "META_VERIFY_TOKEN": self.meta_verify_token != "development_token",
            "META_PHONE_NUMBER_ID": self.meta_phone_number_id != "development_phone_id",
            "TELEGRAM_BOT_TOKEN": bool(self.telegram_bot_token),
            "TELEGRAM_WEBHOOK_SECRET_TOKEN": bool(self.telegram_webhook_secret_token),
        }
        for env_key, ok in checks.items():
            if not ok:
                missing.append(env_key)

        if missing:
            raise RuntimeError(
                "Missing critical runtime configuration for non-dev environment: "
                + ", ".join(sorted(missing))
            )

settings = Settings()
