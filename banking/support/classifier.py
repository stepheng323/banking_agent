"""Support intent classifier using LLM."""

import json
import re
from dataclasses import dataclass

from langchain_core.runnables import Runnable

from banking.intent.routing_signals import (
    looks_like_support_problem_statement,
    looks_like_transaction_replay_modifier_request,
)
from banking.support.models import (
    ClassificationResult,
    SupportIntent,
    TransactionReference,
)
from banking.support.prompts.classifier import SUPPORT_CLASSIFIER_PROMPT
from shared.observability.llm import ainvoke_with_config, build_llm_runnable_config
from shared.utils.logging import get_logger

logger = get_logger(__name__)

INTENT_ALIASES = {
    "transfer_failure_reason": SupportIntent.FAILED_TRANSFER,
    "failure_reason": SupportIntent.FAILED_TRANSFER,
    "support_escalation": SupportIntent.HUMAN_HANDOFF,
    "fraud_suspected": SupportIntent.FRAUD_REPORT,
    "reversal_refund_status": SupportIntent.REVERSAL_REFUND,
    "refund_status": SupportIntent.REVERSAL_REFUND,
    "ticket_update": SupportIntent.TICKET_STATUS,
    "complaint_status": SupportIntent.TICKET_STATUS,
    "retry_payout": SupportIntent.RETRY_TRANSFER,
}

FAILED_TRANSACTION_RE = re.compile(
    r"\b(?:my\s+)?(?:last|latest|recent)?\s*(?:transfer|transaction|payment)\s+(?:failed|fail(?:ed)?)\b"
    r"|\b(?:transfer|transaction|payment)\s+(?:failed|fail(?:ed)?)\b",
    re.IGNORECASE,
)
PENDING_TRANSACTION_RE = re.compile(r"\b(?:pending|processing|stuck|not\s+completed|hasn'?t\s+gone)\b", re.IGNORECASE)
RECEIPT_RE = re.compile(r"\b(?:receipt|proof\s+of\s+payment|payment\s+proof)\b", re.IGNORECASE)
RETRY_RE = re.compile(r"\b(?:retry|try\s+again|send\s+again|resend)\b", re.IGNORECASE)
REFUND_RE = re.compile(r"\b(?:refund|reversal|reverse|money\s+back)\b", re.IGNORECASE)
WRONG_DEBIT_RE = re.compile(
    r"\b(?:debited\s+twice|double\s+debit|wrong(?:ly)?\s+debited|money\s+left)\b"
    r"|\b(?:i\s+(?:was\s+)?debited|money\s+(?:left|deducted)|debit(?:ed)?)\b.*"
    r"\b(?:didn['’]?t|did\s+not|not|never)\s+(?:receive|get|arrive|reflect|go\s+through)\b"
    r"|\b(?:recipient|beneficiary|they|he|she)\s+"
    r"(?:didn['’]?t|did\s+not|not|never)\s+(?:receive|get)\b",
    re.IGNORECASE,
)
FRAUD_RE = re.compile(r"\b(?:fraud|unauthori[sz]ed|wasn'?t\s+me|didn'?t\s+authorize|not\s+me)\b", re.IGNORECASE)
HUMAN_HANDOFF_RE = re.compile(
    r"\b(?:human|agent|support\s+(?:person|team)|talk\s+to\s+support|complain)\b",
    re.IGNORECASE,
)
TICKET_STATUS_RE = re.compile(
    r"\b(?:ticket|complaint|case)\b.*\b(?:status|update|progress|happened)\b"
    r"|\b(?:status|update|progress)\b.*\b(?:ticket|complaint|case)\b",
    re.IGNORECASE,
)
RECENT_REFERENCE_RE = re.compile(r"\b(?:last|latest|most\s+recent|recent)\b", re.IGNORECASE)


@dataclass(frozen=True)
class _ParseOutcome:
    result: ClassificationResult
    usable: bool
    fallback_reason: str | None = None


class SupportClassifier:
    """Classifies user messages into support intents."""

    def __init__(self, llm: Runnable):
        self.llm = llm

    async def classify(self, message: str) -> ClassificationResult:
        """
        Classify message into a support intent.

        Returns ClassificationResult with intent (or None if not support-related).
        """
        prompt = SUPPORT_CLASSIFIER_PROMPT.format(message=message)

        try:
            response = await ainvoke_with_config(
                self.llm,
                prompt,
                config=build_llm_runnable_config(
                    role="support_classifier",
                    task_domain="support",
                    extra_metadata={"prompt_chars": len(prompt)},
                )
                or None,
            )
            content = response.content if hasattr(response, "content") else str(response)

            result, should_fallback = self._parse_response(content, message)
            logger.info(
                "support_classified",
                intent=result.intent.value if result.intent else None,
                confidence=result.confidence,
                source="llm",
            )
            if not should_fallback:
                return result

            if fallback := self._rule_based_fallback(message):
                logger.info(
                    "support_classified",
                    intent=fallback.intent.value if fallback.intent else None,
                    confidence=fallback.confidence,
                    source="deterministic_after_unusable_llm_output",
                )
                return fallback
            return result

        except Exception as e:
            logger.error("support_classification_failed", error=str(e))
            if fallback := self._rule_based_fallback(message):
                logger.info(
                    "support_classified",
                    intent=fallback.intent.value if fallback.intent else None,
                    confidence=fallback.confidence,
                    source="deterministic_after_llm_failure",
                )
                return fallback
            return ClassificationResult(
                intent=None,
                confidence=0.0,
                raw_message=message,
            )

    def _parse_response(self, content: str, original_message: str) -> tuple[ClassificationResult, bool]:
        """Parse LLM response into ClassificationResult."""
        outcome = self._parse_structured_response(content, original_message)
        return outcome.result, not outcome.usable

    def _parse_structured_response(self, content: str, original_message: str) -> _ParseOutcome:
        json_start = content.find("{")
        json_end = content.rfind("}") + 1
        if json_start < 0 or json_end <= json_start:
            return _ParseOutcome(
                result=ClassificationResult(intent=None, confidence=0.0, raw_message=original_message),
                usable=False,
                fallback_reason="missing_json",
            )

        try:
            data = json.loads(content[json_start:json_end])
        except json.JSONDecodeError:
            return _ParseOutcome(
                result=ClassificationResult(intent=None, confidence=0.0, raw_message=original_message),
                usable=False,
                fallback_reason="invalid_json",
            )

        if not isinstance(data, dict) or not data:
            return _ParseOutcome(
                result=ClassificationResult(intent=None, confidence=0.0, raw_message=original_message),
                usable=False,
                fallback_reason="empty_json",
            )

        intent_str = data.get("intent")
        intent = None
        if intent_str not in (None, ""):
            intent = self._parse_intent(str(intent_str))
            if intent is None:
                return _ParseOutcome(
                    result=ClassificationResult(intent=None, confidence=0.0, raw_message=original_message),
                    usable=False,
                    fallback_reason="invalid_intent",
                )

        tx_ref_data = data.get("transaction_ref", {})
        tx_ref = None
        if isinstance(tx_ref_data, dict) and any(tx_ref_data.values()):
            tx_ref = TransactionReference(
                transaction_id=tx_ref_data.get("transaction_id"),
                amount=tx_ref_data.get("amount"),
                recipient_name=tx_ref_data.get("recipient_name"),
                date_hint=tx_ref_data.get("date_hint"),
                use_quoted=bool(tx_ref_data.get("use_quoted", False)),
                use_recent=bool(tx_ref_data.get("use_recent", False)),
            )

        if intent == SupportIntent.RETRY_TRANSFER and not SupportClassifier._retry_intent_is_grounded(
            original_message,
            tx_ref,
        ):
            return _ParseOutcome(
                result=ClassificationResult(
                    intent=None,
                    confidence=self._parse_confidence(data.get("confidence")),
                    raw_message=original_message,
                ),
                usable=True,
            )

        return _ParseOutcome(
            result=ClassificationResult(
                intent=intent,
                confidence=self._parse_confidence(data.get("confidence")),
                transaction_ref=tx_ref,
                raw_message=original_message,
            ),
            usable=True,
        )

    @staticmethod
    def _parse_intent(intent_str: str) -> SupportIntent | None:
        normalized = intent_str.strip().lower()
        if not normalized or normalized == "null":
            return None
        if normalized in INTENT_ALIASES:
            return INTENT_ALIASES[normalized]
        try:
            return SupportIntent(normalized)
        except ValueError:
            return None

    @staticmethod
    def _parse_confidence(value: object) -> float:
        if value is None:
            return 0.5
        if not isinstance(value, (int, float, str)):
            return 0.5
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return 0.5
        return max(0.0, min(1.0, confidence))

    @staticmethod
    def _rule_based_fallback(message: str) -> ClassificationResult | None:
        tx_ref = TransactionReference(use_recent=bool(RECENT_REFERENCE_RE.search(message)))
        if TICKET_STATUS_RE.search(message):
            return ClassificationResult(
                intent=SupportIntent.TICKET_STATUS,
                confidence=0.95,
                transaction_ref=None,
                raw_message=message,
            )
        if FRAUD_RE.search(message):
            return ClassificationResult(
                intent=SupportIntent.FRAUD_REPORT,
                confidence=0.9,
                transaction_ref=tx_ref,
                raw_message=message,
            )
        if RETRY_RE.search(message) and SupportClassifier._retry_intent_is_grounded(message, tx_ref):
            return ClassificationResult(
                intent=SupportIntent.RETRY_TRANSFER,
                confidence=0.9,
                transaction_ref=tx_ref,
                raw_message=message,
            )
        if FAILED_TRANSACTION_RE.search(message):
            return ClassificationResult(
                intent=SupportIntent.FAILED_TRANSFER,
                confidence=0.9,
                transaction_ref=tx_ref if tx_ref.use_recent else TransactionReference(use_recent=True),
                raw_message=message,
            )
        if RECEIPT_RE.search(message):
            return ClassificationResult(
                intent=SupportIntent.RECEIPT_REQUEST,
                confidence=0.9,
                transaction_ref=tx_ref,
                raw_message=message,
            )
        if WRONG_DEBIT_RE.search(message):
            return ClassificationResult(
                intent=SupportIntent.WRONG_DEBIT,
                confidence=0.9,
                transaction_ref=tx_ref,
                raw_message=message,
            )
        if REFUND_RE.search(message):
            return ClassificationResult(
                intent=SupportIntent.REVERSAL_REFUND,
                confidence=0.9,
                transaction_ref=tx_ref,
                raw_message=message,
            )
        if PENDING_TRANSACTION_RE.search(message):
            return ClassificationResult(
                intent=SupportIntent.PENDING_TRANSFER,
                confidence=0.85,
                transaction_ref=tx_ref,
                raw_message=message,
            )
        if HUMAN_HANDOFF_RE.search(message):
            return ClassificationResult(
                intent=SupportIntent.HUMAN_HANDOFF,
                confidence=0.85,
                transaction_ref=None,
                raw_message=message,
            )
        return None

    @staticmethod
    def _retry_intent_is_grounded(message: str, tx_ref: TransactionReference | None) -> bool:
        if looks_like_transaction_replay_modifier_request(message):
            return False
        if tx_ref is not None and (
            tx_ref.transaction_id
            or tx_ref.amount is not None
            or tx_ref.recipient_name
            or tx_ref.date_hint
            or tx_ref.use_quoted
            or tx_ref.use_recent
        ):
            return True
        return looks_like_support_problem_statement(message)

    def is_support_intent(self, result: ClassificationResult) -> bool:
        """Check if classification result is a valid support intent."""
        return result.intent is not None and result.confidence >= 0.5
