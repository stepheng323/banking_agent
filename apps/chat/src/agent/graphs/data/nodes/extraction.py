import re
from typing import Any

from apps.chat.src.agent.graphs.__shared__.extraction_utils import try_extract_numeric_index
from apps.chat.src.agent.graphs.__shared__.scheduling import (
    SCHEDULE_FIELD_NAMES,
    parse_schedule_slot_patch,
    schedule_required_prompt,
)
from apps.chat.src.agent.graphs.data.models.types import DataContext, DataGates, DataPayload
from apps.chat.src.agent.graphs.data.pipeline.base import PipelineStep
from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
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


def _resolved_phone_referent(context: DataContext) -> dict[str, Any] | None:
    resolution = context.resolved_referents.get("phone")
    if not isinstance(resolution, dict) or resolution.get("status") != "resolved":
        return None
    item = resolution.get("item")
    if not isinstance(item, dict):
        return None
    data = item.get("data")
    return data if isinstance(data, dict) else None


def _ambiguous_phone_referent_prompt(context: DataContext) -> str | None:
    resolution = context.resolved_referents.get("phone")
    if not isinstance(resolution, dict) or resolution.get("status") != "ambiguous":
        return None
    raw_candidates = resolution.get("candidates")
    candidates = raw_candidates if isinstance(raw_candidates, list) else []
    lines: list[str] = []
    for idx, item in enumerate(candidates[:5], start=1):
        if not isinstance(item, dict):
            continue
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        phone = str(data.get("phone") or data.get("target_phone") or item.get("label") or "").strip()
        if phone:
            lines.append(f"{idx}. {phone}")
    if not lines:
        return None
    return "Which number did you mean?\n" + "\n".join(lines)


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
        if not payload.target_phone:
            ambiguity_prompt = _ambiguous_phone_referent_prompt(context)
            if ambiguity_prompt:
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["target_phone"],
                    prompt=ambiguity_prompt,
                    patch=payload.model_dump(exclude_none=True),
                )
        schedule_required_fields = [field for field in required_fields if field in SCHEDULE_FIELD_NAMES]
        if schedule_required_fields:
            schedule_patch, remaining_schedule_fields = parse_schedule_slot_patch(
                self.user_message,
                schedule_required_fields,
            )
            if schedule_patch:
                for field, value in schedule_patch.items():
                    if field == "confirmation":
                        payload.confirmation = value
                    elif hasattr(payload, field):
                        setattr(payload, field, value)
                payload.skip_extraction = False
                return None
            if len(schedule_required_fields) == len(required_fields):
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=remaining_schedule_fields or schedule_required_fields,
                    prompt=schedule_required_prompt(remaining_schedule_fields or schedule_required_fields, context.language),
                    patch={"is_scheduled_operation": True, "skip_finalize_summary": True},
                )
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
            phone_referent = _resolved_phone_referent(context)
            phone = normalize_nigerian_phone(str((phone_referent or {}).get("phone") or ""))
            if phone and not payload.target_phone:
                payload.target_phone = phone
                if not payload.network and phone_referent and phone_referent.get("network"):
                    normalized_network = normalize_network_name(str(phone_referent["network"]))
                    payload.network = normalized_network or str(phone_referent["network"]).strip().upper()
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
        elif not payload.target_phone:
            phone_referent = _resolved_phone_referent(context)
            phone = normalize_nigerian_phone(str((phone_referent or {}).get("phone") or ""))
            if phone:
                payload.target_phone = phone

        if extraction_result.entities.network:
            payload.network = extraction_result.entities.network
        elif not payload.network and payload.target_phone:
            phone_referent = _resolved_phone_referent(context)
            if phone_referent and phone_referent.get("network"):
                normalized_network = normalize_network_name(str(phone_referent["network"]))
                payload.network = normalized_network or str(phone_referent["network"]).strip().upper()

        # TODO: Handle 'amount' or 'budget' text to float mapping more robustly if needed
        # For now assuming simple mapping usually happens in resolution or prior

        payload.stage = "extracted"
        return None  # Continue pipeline
