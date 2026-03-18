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
        self.planner_model: str = os.getenv("PLANNER_MODEL", "gpt-4o-mini")
        self.interrupt_router_model: str = os.getenv("INTERRUPT_ROUTER_MODEL", self.planner_model).strip()

        self.flutterwave_secret_key: str = os.getenv("FLUTTERWAVE_SECRET_KEY", "")
        self.flutterwave_use_sandbox: bool = os.getenv("FLUTTERWAVE_USE_SANDBOX", "false").lower() == "true"

        self.mono_api_key: str = os.getenv("MONO_API_KEY", "")
        self.mono_use_mock_override: bool | None = self._parse_optional_bool(os.getenv("MONO_USE_MOCK"))

        self.s3_bucket_name: str = os.getenv("S3_BUCKET_NAME", "")
        self.aws_region: str = os.getenv("AWS_REGION", "us-east-1")
        self.aws_account_id: str = os.getenv("AWS_ACCOUNT_ID", "000000000000")
        self.s3_region: str = self.aws_region
        self.s3_receipt_prefix: str = "receipts"
        self.runtime_stack_role: str = os.getenv("RUNTIME_STACK_ROLE", "aws-primary").strip() or "aws-primary"
        self.chat_transport: str = os.getenv("CHAT_TRANSPORT", "redis").strip() or "redis"
        self.async_transport: str = os.getenv("ASYNC_TRANSPORT", "aws").strip() or "aws"
        self.enable_webhook_ingress: bool = os.getenv("ENABLE_WEBHOOK_INGRESS", "true").lower() == "true"
        self.enable_chat_consumers: bool = os.getenv("ENABLE_CHAT_CONSUMERS", "true").lower() == "true"
        self.enable_transaction_worker: bool = os.getenv("ENABLE_TRANSACTION_WORKER", "false").lower() == "true"
        self.enable_funding_worker: bool = os.getenv("ENABLE_FUNDING_WORKER", "false").lower() == "true"
        self.enable_payout_worker: bool = os.getenv("ENABLE_PAYOUT_WORKER", "false").lower() == "true"
        self.enable_refund_worker: bool = os.getenv("ENABLE_REFUND_WORKER", "false").lower() == "true"
        self.enable_receipt_worker: bool = os.getenv("ENABLE_RECEIPT_WORKER", "false").lower() == "true"
        self.enable_outbound_sender: bool = os.getenv("ENABLE_OUTBOUND_SENDER", "true").lower() == "true"
        self.chat_message_max_age_seconds: int = int(os.getenv("CHAT_MESSAGE_MAX_AGE_SECONDS", "120"))
        self.sqs_wait_time_seconds: int = int(os.getenv("SQS_WAIT_TIME_SECONDS", "10"))
        self.sqs_visibility_timeout_seconds: int = int(os.getenv("SQS_VISIBILITY_TIMEOUT_SECONDS", "90"))
        self.sqs_poll_max_messages: int = int(os.getenv("SQS_POLL_MAX_MESSAGES", "5"))

        self.default_channel: str = os.getenv("DEFAULT_CHANNEL", "whatsapp")

        self.telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_mini_app_base_url: str = os.getenv("TELEGRAM_MINI_APP_BASE_URL", "")
        self.telegram_webhook_secret_token: str = os.getenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "")
        self.telegram_enable_message_draft: bool = os.getenv("TELEGRAM_ENABLE_MESSAGE_DRAFT", "true").lower() == "true"
        self.receipt_verification_base_url: str = os.getenv("RECEIPT_VERIFICATION_BASE_URL", "").strip()
        raw_whatsapp_allowed_numbers = os.getenv("WHATSAPP_ALLOWED_NUMBERS", "")
        self.whatsapp_allowed_numbers: set[str] = {
            n.strip() for n in raw_whatsapp_allowed_numbers.split(",") if n.strip()
        }

        self.soul_policy_path: str = os.getenv("SOUL_POLICY_PATH", "config/soul_policy.json")
        self.enable_channel_option_ux_v2: bool = os.getenv("ENABLE_CHANNEL_OPTION_UX_V2", "false").lower() == "true"
        self.enable_transfer_scheduling: bool = os.getenv("ENABLE_TRANSFER_SCHEDULING", "true").lower() == "true"
        self.schedule_dispatcher_batch_size: int = int(os.getenv("SCHEDULE_DISPATCHER_BATCH_SIZE", "25"))
        self.schedule_max_due_per_tick: int = int(os.getenv("SCHEDULE_MAX_DUE_PER_TICK", "25"))
        self.schedule_retry_delay_minutes: int = int(os.getenv("SCHEDULE_RETRY_DELAY_MINUTES", "1"))
        self.async_housekeeping_max_concurrency: int = int(os.getenv("ASYNC_HOUSEKEEPING_MAX_CONCURRENCY", "4"))
        self.async_housekeeping_max_retries: int = int(os.getenv("ASYNC_HOUSEKEEPING_MAX_RETRIES", "0"))
        self.async_housekeeping_retry_base_ms: int = int(os.getenv("ASYNC_HOUSEKEEPING_RETRY_BASE_MS", "120"))
        self.checkpoint_ttl_maintenance_interval_seconds: int = int(
            os.getenv("CHECKPOINT_TTL_MAINTENANCE_INTERVAL_SECONDS", "300")
        )
        self.latency_slo_p50_ms: int = int(os.getenv("LATENCY_SLO_P50_MS", "900"))
        self.latency_slo_p95_ms: int = int(os.getenv("LATENCY_SLO_P95_MS", "2500"))
        self.latency_slo_p99_ms: int = int(os.getenv("LATENCY_SLO_P99_MS", "4500"))
        self.latency_slo_error_rate_threshold: float = float(os.getenv("LATENCY_SLO_ERROR_RATE_THRESHOLD", "0.05"))

        self._validate_critical_runtime_config()
        self._validate_whatsapp_config()

    @staticmethod
    def _parse_optional_bool(raw: str | None) -> bool | None:
        """Parse optional boolean env vars."""
        if raw is None:
            return None
        normalized = raw.strip().lower()
        if not normalized:
            return None
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise RuntimeError(f"Invalid boolean value: {raw}")

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
                "Missing critical runtime configuration for non-dev environment: " + ", ".join(sorted(missing))
            )

    @property
    def use_mono_mock(self) -> bool:
        """Return whether Mono clients should use mock responses."""
        if self.mono_use_mock_override is not None:
            return self.mono_use_mock_override
        return self.app_env.lower() == "development"

    @property
    def selected_direct_debit_provider(self) -> str:
        """Return the effective direct-debit provider selection."""
        return "mock" if self.use_mono_mock else "mono"

    @property
    def uses_aws_async_transport(self) -> bool:
        """Return whether async queue publishing/consumption should use AWS SNS/SQS."""
        return self.async_transport.lower() == "aws"

    @property
    def is_passive_runtime(self) -> bool:
        """Return whether this runtime is configured as a passive standby."""
        return not any(
            (
                self.enable_webhook_ingress,
                self.enable_chat_consumers,
                self.enable_transaction_worker,
                self.enable_funding_worker,
                self.enable_payout_worker,
                self.enable_refund_worker,
                self.enable_receipt_worker,
                self.enable_outbound_sender,
            )
        )


settings = Settings()
