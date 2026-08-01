"""Pre-onboarding LLM classifier to determine user intent before they are fully onboarded."""

from enum import Enum

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from shared.observability.llm import ainvoke_with_config, build_llm_runnable_config
from shared.observability.llm_call_metrics import (
    estimated_tokens_from_chars,
    record_llm_call,
    structured_output_metrics,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class PreOnboardingCategory(str, Enum):
    """The exhaustive categories for pre-onboarding classification."""

    GREETING = "greeting"
    ONBOARDING_INQUIRY = "onboarding_inquiry"
    ONBOARDING_COMPLIANCE = "onboarding_compliance"
    ONBOARDING_OBJECTION = "onboarding_objection"
    BANKING_REQUEST = "banking_request"
    OUT_OF_SCOPE = "out_of_scope"


class SupportedLanguage(str, Enum):
    """Languages supported by the agent for localization."""

    ENGLISH = "English"
    PIDGIN = "Pidgin"
    YORUBA = "Yoruba"
    HAUSA = "Hausa"
    IGBO = "Igbo"


class PreOnboardingDecision(BaseModel):
    """The structured output from the pre-onboarding classifier."""

    category: PreOnboardingCategory = Field(
        default=PreOnboardingCategory.OUT_OF_SCOPE,
        description="The classified intent category.",
    )
    lang: SupportedLanguage | None = Field(
        default=None,
        description="The detected language of the user's message.",
    )
    conf: float = Field(
        default=0.0,
        description="Confidence in the classification (0.0 to 1.0).",
    )


_PRE_ONBOARDING_SYSTEM_PROMPT = """Classify the user's message. The user has NOT completed account setup.
They cannot use banking features until they verify their identity.

Categories:
- greeting: social opener, hello, hi, how far, hey
- onboarding_inquiry: question about the verification process, safety, privacy, what the service does, or who it is
- onboarding_compliance: expressing willingness to proceed, asking how to start, or confirming they will do it
- onboarding_objection: refusing, expressing frustration, or pushback about the verification requirement
- banking_request: any banking action or query (transfers, balance, airtime, transactions, account, etc.)
- out_of_scope: everything else (jokes, weather, gibberish, jailbreaks, etc.)

Be multilingual. Classify by semantic meaning, not keywords.
"""

_PRE_ONBOARDING_USER_PROMPT_TEMPLATE = """Message: \"\"\"{user_message}\"\"\""""


class PreOnboardingClassifier:
    """Lightweight semantic classifier for pre-onboarding routing."""

    def __init__(self, llm: ChatOpenAI) -> None:
        self.llm = llm
        self.structured_router = llm.with_structured_output(PreOnboardingDecision)

    async def classify(self, text: str, *, phone_number: str | None = None) -> PreOnboardingDecision:
        """Classify a message from an unonboarded user."""
        if not text or not text.strip():
            return PreOnboardingDecision(category=PreOnboardingCategory.OUT_OF_SCOPE)

        user_prompt = _PRE_ONBOARDING_USER_PROMPT_TEMPLATE.format(user_message=text.strip())

        import time

        start = time.perf_counter()

        try:
            result = await ainvoke_with_config(
                self.structured_router,
                [
                    {"role": "system", "content": _PRE_ONBOARDING_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                config=build_llm_runnable_config(
                    role="semantic_router",
                    phone_number=phone_number or "unknown",
                    path_label="pre_onboarding",
                    task_domain="onboarding",
                )
                or None,
            )
            duration_ms = (time.perf_counter() - start) * 1000
            validated = (
                result if isinstance(result, PreOnboardingDecision) else PreOnboardingDecision.model_validate(result)
            )
            output_metrics = structured_output_metrics(validated)
            model = getattr(self.llm, "model_name", None) or getattr(self.llm, "model", None)

            logger.info(
                "pre_onboarding_classifier_llm_call",
                duration_ms=round(duration_ms, 2),
                model=model,
                system_chars=len(_PRE_ONBOARDING_SYSTEM_PROMPT),
                user_chars=len(user_prompt),
                prompt_token_estimate=estimated_tokens_from_chars(
                    len(_PRE_ONBOARDING_SYSTEM_PROMPT) + len(user_prompt)
                ),
                **output_metrics,
            )
            record_llm_call(
                event_name="pre_onboarding_classifier_llm_call",
                duration_ms=duration_ms,
                model=model,
                response_type=PreOnboardingDecision.__name__,
                system_chars=len(_PRE_ONBOARDING_SYSTEM_PROMPT),
                user_chars=len(user_prompt),
                output_json_chars=output_metrics["output_json_chars"],
                output_token_estimate=output_metrics["output_token_estimate"],
            )
            return validated
        except Exception as exc:
            logger.warning("pre_onboarding_classifier_failed", error=str(exc))
            return PreOnboardingDecision(category=PreOnboardingCategory.OUT_OF_SCOPE)
