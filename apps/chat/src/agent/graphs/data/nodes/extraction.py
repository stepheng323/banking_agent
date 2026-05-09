import re
from typing import Any

from apps.chat.src.agent.graphs.__shared__.extraction_utils import try_extract_numeric_index
from apps.chat.src.agent.graphs.data.models.types import DataContext, DataGates, DataPayload
from apps.chat.src.agent.graphs.data.pipeline.base import PipelineStep
from apps.chat.src.agent.orchestrator.models.domain import TransactionResult
from shared.utils.logging import get_logger
from shared.utils.network_utils import normalize_network_name, normalize_nigerian_phone

logger = get_logger(__name__)
_NETWORK_CANONICAL = {"MTN", "AIRTEL", "GLO", "9MOBILE"}
_PHONE_CANDIDATE_PATTERN = re.compile(r"(?:\+?234|0)?(?:[\s().-]*\d){10,13}")
_NETWORK_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")


def _has_phone_signal(message: str) -> bool:
    return any(normalize_nigerian_phone(candidate) for candidate in _PHONE_CANDIDATE_PATTERN.findall(message))


def _has_network_signal(message: str) -> bool:
    for token in _NETWORK_TOKEN_PATTERN.findall(message):
        normalized = normalize_network_name(token)
        if normalized:
            return True
        if token.strip().upper() in _NETWORK_CANONICAL:
            return True
    return False


def _has_resolved_network(network: str | None) -> bool:
    if not network:
        return False
    normalized = normalize_network_name(network)
    if normalized:
        return True
    return network.strip().upper() in _NETWORK_CANONICAL


def _skip_override_reason(payload: DataPayload, message: str) -> str | None:
    normalized_phone = normalize_nigerian_phone(str(payload.target_phone or ""))
    if normalized_phone is None and _has_phone_signal(message):
        return "missing_target_phone_with_phone_signal"
    if not _has_resolved_network(payload.network) and _has_network_signal(message):
        return "missing_network_with_network_signal"
    return None


class ExtractionStep(PipelineStep):
    """Extraction Step: Parse user message into DataPayload."""

    def __init__(self, user_message: str | None):
        self.user_message = user_message

    async def run(
        self, payload: DataPayload, context: DataContext, gates: DataGates, worker_context: Any
    ) -> TransactionResult | None:
        del gates
        if not self.user_message:
            return None

        # [DETERMINISTIC FALLBACK] Numeric index selection
        # If user replies with "1" or "2" while selecting source account, map it directly.
        raw_required_fields = getattr(worker_context, "required_fields", [])
        required_fields = raw_required_fields if isinstance(raw_required_fields, list) else []
        waiting_for_source_account = "source_account_id" in required_fields
        numeric_patch = try_extract_numeric_index(self.user_message, "data") if waiting_for_source_account else None
        if numeric_patch:
            payload.source_account_index = numeric_patch["source_account_index"]
            payload.stage = "extracted"
            return None

        if payload.skip_extraction:
            override_reason = _skip_override_reason(payload, self.user_message)
            payload.skip_extraction = False
            if override_reason is None:
                logger.info("skip_redundant_extraction", task="data", reason="no_override_signal")
                return None
            logger.info("override_skip_extraction", task="data", reason=override_reason)

        extractor = worker_context.extractor
        if not extractor:
            logger.info("data_extraction_skipped", reason="extractor_unavailable")
            return None
        extraction_result = await extractor.extract(
            self.user_message,
            smart_context={
                "previousResponse": getattr(worker_context, "previous_response", None),
                "required_fields": required_fields,
                "beneficiaries": context.beneficiaries,
                "accounts": context.accounts,
                "language": context.language,
                "target_phone": payload.target_phone,
                "network": payload.network,
                "plan_name": payload.plan_name,
                "amount": payload.amount,
            },
        )

        payload.extraction = extraction_result

        if extraction_result.entities.recipient_phone:
            payload.target_phone = extraction_result.entities.recipient_phone

        if extraction_result.entities.network:
            payload.network = extraction_result.entities.network

        # TODO: Handle 'amount' or 'budget' text to float mapping more robustly if needed
        # For now assuming simple mapping usually happens in resolution or prior

        payload.stage = "extracted"
        return None  # Continue pipeline
