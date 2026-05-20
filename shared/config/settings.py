"""Shared application configuration."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

LOCAL_APP_ENVS = frozenset({"dev", "development", "test", "local"})


def _load_local_dotenv() -> None:
    """Load .env only for local runtimes, without overriding deployment env vars."""
    raw_app_env = os.environ.get("APP_ENV")
    if raw_app_env is not None and raw_app_env.strip().lower() not in LOCAL_APP_ENVS:
        return

    _env_path = Path(__file__).parent.parent.parent / ".env"
    load_dotenv(dotenv_path=_env_path, override=False)


_load_local_dotenv()


@dataclass
class RuntimeConfig:
    """Runtime identity and environment classification."""

    app_env: str
    infrastructure_environment: str

    @property
    def is_local(self) -> bool:
        """Return whether APP_ENV represents a local/test runtime."""
        return self.app_env.strip().lower() in LOCAL_APP_ENVS

    @property
    def is_production(self) -> bool:
        """Return whether APP_ENV represents production."""
        return self.app_env.strip().lower() == "production"


@dataclass
class WhatsAppConfig:
    """WhatsApp and Meta Flow configuration."""

    runtime: RuntimeConfig
    verify_token: str
    app_secret: str
    access_token: str
    phone_number_id: str
    flow_private_key: str
    onboarding_flow_id: str
    account_linking_flow_id: str
    pin_confirmation_flow_id: str
    allowed_numbers: set[str]
    typing_indicator_delay_ms: int

    @property
    def require_encrypted_flows(self) -> bool:
        """Return whether WhatsApp Flow data exchange requests must be encrypted."""
        return not self.runtime.is_local


class Settings:
    """Application settings loaded from environment variables and .env file."""

    def __init__(self) -> None:
        self.runtime = RuntimeConfig(
            app_env=os.getenv("APP_ENV", "development"),
            infrastructure_environment=os.getenv("ENVIRONMENT", "dev"),
        )
        raw_whatsapp_allowed_numbers = os.getenv("WHATSAPP_ALLOWED_NUMBERS", "")
        self.whatsapp = WhatsAppConfig(
            runtime=self.runtime,
            verify_token=os.getenv("META_VERIFY_TOKEN", "development_token"),
            app_secret=os.getenv("META_APP_SECRET", "").strip(),
            access_token=os.getenv("META_ACCESS_TOKEN", "development_access_token"),
            phone_number_id=os.getenv("META_PHONE_NUMBER_ID", "development_phone_id"),
            flow_private_key=os.getenv("WHATSAPP_FLOW_PRIVATE_KEY", "").strip(),
            onboarding_flow_id=os.getenv("ONBOARDING_FLOW_ID", ""),
            account_linking_flow_id=os.getenv("ACCOUNT_LINKING_FLOW_ID", ""),
            pin_confirmation_flow_id=os.getenv("PIN_CONFIRMATION_FLOW_ID", ""),
            allowed_numbers={n.strip() for n in raw_whatsapp_allowed_numbers.split(",") if n.strip()},
            typing_indicator_delay_ms=int(os.getenv("WHATSAPP_TYPING_INDICATOR_DELAY_MS", "650")),
        )

        self.project_name: str = os.getenv("PROJECT_NAME", "banking-agent")
        self.app_host: str = os.getenv("APP_HOST", "0.0.0.0")
        self.app_port: int = int(os.getenv("APP_PORT", "8000"))
        self.app_name: str = os.getenv("APP_NAME", "Narya AI").strip() or "Narya AI"
        self.app_name_short: str = os.getenv("APP_NAME_SHORT", "").strip() or self.app_name.split()[0]
        self.app_creator: str = os.getenv("APP_CREATOR", "Narya AI team").strip() or "Narya AI team"
        self.app_brand_inspiration: str = (
            os.getenv("APP_BRAND_INSPIRATION", "Narya from The Lord of the Rings").strip()
            or "Narya from The Lord of the Rings"
        )
        self.app_public_base_url: str = (
            os.getenv("APP_PUBLIC_BASE_URL", "https://narya.ai").strip().rstrip("/") or "https://narya.ai"
        )

        self.database_url: str = os.getenv("DATABASE_URL", "sqlite:///./test.db")
        self.db_pool_size: int = int(os.getenv("DB_POOL_SIZE", "20"))
        self.db_max_overflow: int = int(os.getenv("DB_MAX_OVERFLOW", "10"))
        self.db_pool_timeout: int = int(os.getenv("DB_POOL_TIMEOUT", "30"))

        self.redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379")

        self.user_ctx_ttl_seconds: int = int(os.getenv("TTL_SECONDS", "6000"))

        self.flow_session_timeout: int = int(os.getenv("FLOW_SESSION_TIMEOUT", "600"))

        self.pending_transaction_ttl: int = int(os.getenv("PENDING_TRANSACTION_TTL", "300"))

        self.openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
        self.planner_model: str = os.getenv("PLANNER_MODEL", "gpt-4o-mini")
        self.query_model: str = os.getenv("QUERY_MODEL", self.planner_model).strip()
        self.interrupt_router_model: str = os.getenv("INTERRUPT_ROUTER_MODEL", self.planner_model).strip()
        self.semantic_router_model: str = os.getenv("SEMANTIC_ROUTER_MODEL", "gpt-5.4-nano").strip()
        self.extractor_model: str = os.getenv("EXTRACTOR_MODEL", "gpt-5.4-mini").strip()
        self.media_image_model: str = os.getenv("MEDIA_IMAGE_MODEL", "gpt-5-mini").strip()
        self.audio_transcription_model: str = os.getenv("AUDIO_TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe").strip()
        self.media_image_max_bytes: int = int(os.getenv("MEDIA_IMAGE_MAX_BYTES", "5000000"))

        self.flutterwave_secret_key: str = os.getenv("FLUTTERWAVE_SECRET_KEY", "")
        self.flutterwave_use_sandbox: bool = os.getenv("FLUTTERWAVE_USE_SANDBOX", "false").lower() == "true"

        self.mono_api_key: str = os.getenv("MONO_API_KEY", "")
        self.mono_webhook_secret: str = os.getenv("MONO_WEBHOOK_SECRET", "").strip()
        self.mono_use_mock_override: bool | None = self._parse_optional_bool(os.getenv("MONO_USE_MOCK"))

        self.s3_bucket_name: str = os.getenv("S3_BUCKET_NAME", "")
        self.aws_region: str = os.getenv("AWS_REGION", "us-east-1")
        self.aws_account_id: str = os.getenv("AWS_ACCOUNT_ID", "000000000000")
        self.s3_region: str = self.aws_region
        self.s3_receipt_prefix: str = "receipts"
        self.chat_transport: str = os.getenv("CHAT_TRANSPORT", "redis").strip() or "redis"
        self.async_transport: str = os.getenv("ASYNC_TRANSPORT", "aws").strip() or "aws"
        self.chat_message_max_age_seconds: int = int(os.getenv("CHAT_MESSAGE_MAX_AGE_SECONDS", "120"))
        self.chat_thread_lock_ttl_seconds: int = int(os.getenv("CHAT_THREAD_LOCK_TTL_SECONDS", "120"))
        self.chat_thread_lock_renew_seconds: int = int(os.getenv("CHAT_THREAD_LOCK_RENEW_SECONDS", "30"))
        self.chat_thread_lock_wait_seconds: int = int(os.getenv("CHAT_THREAD_LOCK_WAIT_SECONDS", "60"))
        self.chat_worker_max_concurrency: int = int(os.getenv("CHAT_WORKER_MAX_CONCURRENCY", "8"))
        self.chat_pending_input_prompt_debounce_seconds: float = float(
            os.getenv("CHAT_PENDING_INPUT_PROMPT_DEBOUNCE_SECONDS", "1.5")
        )
        self.chat_latest_inbound_ttl_seconds: int = int(os.getenv("CHAT_LATEST_INBOUND_TTL_SECONDS", "300"))
        self.sqs_wait_time_seconds: int = int(os.getenv("SQS_WAIT_TIME_SECONDS", "10"))
        self.sqs_visibility_timeout_seconds: int = int(os.getenv("SQS_VISIBILITY_TIMEOUT_SECONDS", "90"))
        self.sqs_poll_max_messages: int = int(os.getenv("SQS_POLL_MAX_MESSAGES", "5"))

        self.default_channel: str = os.getenv("DEFAULT_CHANNEL", "whatsapp")

        self.telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_mini_app_base_url: str = os.getenv("TELEGRAM_MINI_APP_BASE_URL", "")
        self.telegram_webhook_secret_token: str = os.getenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "")
        self.telegram_init_data_max_age_seconds: int = int(os.getenv("TELEGRAM_INIT_DATA_MAX_AGE_SECONDS", "600"))
        self.telegram_enable_message_draft: bool = os.getenv("TELEGRAM_ENABLE_MESSAGE_DRAFT", "true").lower() == "true"
        self.telegram_typing_indicator_delay_ms: int = int(os.getenv("TELEGRAM_TYPING_INDICATOR_DELAY_MS", "650"))
        self.receipt_verification_base_url: str = os.getenv("RECEIPT_VERIFICATION_BASE_URL", "").strip()

        self.assistant_profile_path: str = os.getenv("ASSISTANT_PROFILE_PATH", "config/assistant_profile.json")
        self.capability_policy_path: str = os.getenv("CAPABILITY_POLICY_PATH", "config/capability_policy.json")
        self.domain_guardrails_path: str = os.getenv("DOMAIN_GUARDRAILS_PATH", "config/domain_guardrails.json")
        self.enable_channel_option_ux_v2: bool = os.getenv("ENABLE_CHANNEL_OPTION_UX_V2", "false").lower() == "true"
        self.enable_support_diagnostic_agent: bool = (
            os.getenv("ENABLE_SUPPORT_DIAGNOSTIC_AGENT", "false").lower() == "true"
        )
        self.enable_unified_transaction_view: bool = bool(
            self._parse_optional_bool(os.getenv("ENABLE_UNIFIED_TRANSACTION_VIEW"))
        )
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

        if self.whatsapp.access_token == "development_access_token":
            warnings.append("META_ACCESS_TOKEN is not set - using development default")

        if not self.whatsapp.app_secret:
            warnings.append("META_APP_SECRET is not set")

        if self.whatsapp.phone_number_id == "development_phone_id":
            warnings.append("META_PHONE_NUMBER_ID is not set - using development default")

        if not self.whatsapp.onboarding_flow_id:
            warnings.append("ONBOARDING_FLOW_ID is using default - update with your actual Flow ID")

        if warnings and not self.runtime.is_local:
            print("⚠️  Configuration warnings:")
            for warning in warnings:
                print(f"   - {warning}")
            print("   Create a .env file with proper WhatsApp credentials to fix these warnings.")

    def _validate_critical_runtime_config(self) -> None:
        """Fail fast outside dev/test when critical runtime config is missing."""
        if self.runtime.is_local:
            return

        missing: list[str] = []

        checks = {
            "DATABASE_URL": self.database_url and self.database_url != "sqlite:///./test.db",
            "REDIS_URL": self.redis_url and self.redis_url != "redis://localhost:6379",
            "OPENAI_API_KEY": bool(self.openai_api_key),
            "MONO_API_KEY": bool(self.mono_api_key),
            "MONO_WEBHOOK_SECRET": bool(self.mono_webhook_secret),
            "FLUTTERWAVE_SECRET_KEY": bool(self.flutterwave_secret_key),
            "META_APP_SECRET": bool(self.whatsapp.app_secret),
            "META_ACCESS_TOKEN": self.whatsapp.access_token != "development_access_token",
            "META_VERIFY_TOKEN": self.whatsapp.verify_token != "development_token",
            "META_PHONE_NUMBER_ID": self.whatsapp.phone_number_id != "development_phone_id",
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
        return self.runtime.app_env.lower() == "development"

    @property
    def selected_direct_debit_provider(self) -> str:
        """Return the effective direct-debit provider selection."""
        return "mock" if self.use_mono_mock else "mono"

    @property
    def uses_aws_async_transport(self) -> bool:
        """Return whether async queue publishing/consumption should use AWS SNS/SQS."""
        return self.async_transport.lower() == "aws"


settings = Settings()
