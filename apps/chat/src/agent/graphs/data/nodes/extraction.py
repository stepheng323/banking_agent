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
from shared.i18n import render_message
from shared.utils.logging import get_logger
from shared.utils.network_utils import normalize_network_name, normalize_nigerian_phone

logger = get_logger(__name__)
_NETWORK_CANONICAL = {"MTN", "AIRTEL", "GLO", "9MOBILE"}
_PHONE_CANDIDATE_PATTERN = re.compile(r"(?:\+?234|0)?(?:[\s().-]*\d){10,13}")
_NETWORK_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")
_SELF_TARGET_RE = re.compile(
    r"^(?:for\s+)?(?:me|my\s+(?:line|number|phone)|mine|myself|this\s+line)$",
    re.IGNORECASE,
)
_NETWORK_REPLY_BLOCK_RE = re.compile(
    r"\b(send|transfer|pay|buy|data|airtime|bundle|balance|statement|transaction|transactions|"
    r"account|support|faq|cancel|stop|show|list|check)\b",
    re.IGNORECASE,
)


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


def _first_normalized_phone(message: str) -> str | None:
    for candidate in _PHONE_CANDIDATE_PATTERN.findall(message):
        normalized = normalize_nigerian_phone(candidate)
        if normalized:
            return normalized
    return None


def _self_target_phone(message: str, context: DataContext) -> str | None:
    if not _SELF_TARGET_RE.fullmatch(message.strip()):
        return None
    return normalize_nigerian_phone(context.phone_number) or context.phone_number or None


def _network_reply(message: str) -> str | None:
    text = message.strip()
    if not text or "?" in text or _NETWORK_REPLY_BLOCK_RE.search(text):
        return None
    normalized = normalize_network_name(text)
    return normalized or (text.upper() if text.upper() in _NETWORK_CANONICAL else None)


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
    return _resolved_referent_data(context, "phone")


def _resolved_referent_data(context: DataContext, referent_type: str) -> dict[str, Any] | None:
    resolution = context.resolved_referents.get(referent_type)
    if not isinstance(resolution, dict) or resolution.get("status") != "resolved":
        return None
    item = resolution.get("item")
    if not isinstance(item, dict):
        return None
    data = item.get("data")
    return data if isinstance(data, dict) else None


def _apply_resolved_referents(payload: DataPayload, context: DataContext) -> bool:
    changed = False
    if not payload.target_phone:
        phone_referent = _resolved_phone_referent(context)
        phone = normalize_nigerian_phone(str((phone_referent or {}).get("phone") or ""))
        if phone:
            payload.target_phone = phone
            changed = True
            if not payload.network and phone_referent and phone_referent.get("network"):
                normalized_network = normalize_network_name(str(phone_referent["network"]))
                payload.network = normalized_network or str(phone_referent["network"]).strip().upper()

    if payload.amount is None:
        amount_referent = _resolved_referent_data(context, "amount")
        if amount_referent:
            try:
                amount = float(amount_referent.get("amount"))
            except (TypeError, ValueError):
                amount = None
            if amount is not None and amount > 0:
                payload.amount = amount
                changed = True

    source_has_value = any(
        (
            payload.source_account_id,
            payload.source_bank_name,
            payload.source_account_number,
            payload.source_account_index is not None,
        )
    )
    if not source_has_value:
        source_referent = _resolved_referent_data(context, "source_account")
        if source_referent:
            account_id = source_referent.get("source_account_id") or source_referent.get("account_id")
            bank_name = source_referent.get("source_bank_name") or source_referent.get("bank_name")
            account_name = source_referent.get("source_account_name") or source_referent.get("account_name")
            account_number = source_referent.get("source_account_number") or source_referent.get("account_number")
            if account_id or bank_name or account_number:
                payload.source_account_id = str(account_id).strip() if account_id else None
                payload.source_bank_name = bank_name
                payload.source_account_name = account_name
                payload.source_account_number = account_number
                payload.source_account_index = None
                changed = True

    return changed


def _referent_phone_candidates(context: DataContext) -> list[dict[str, Any]]:
    resolution = context.resolved_referents.get("phone")
    if not isinstance(resolution, dict) or resolution.get("status") != "ambiguous":
        return []
    raw_candidates = resolution.get("candidates")
    raw_items = raw_candidates if isinstance(raw_candidates, list) else []
    candidates: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_items[:5], start=1):
        if not isinstance(item, dict):
            continue
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        phone = normalize_nigerian_phone(str(data.get("phone") or data.get("target_phone") or item.get("label") or ""))
        if phone:
            network = data.get("network")
            label = str(item.get("label") or data.get("recipient_name") or phone).strip()
            display = f"{label} • {phone}" if label and label != phone else phone
            if network:
                display = f"{display} • {str(network).strip().upper()}"
            candidates.append(
                {
                    "index": idx,
                    "option_id": f"phone:{phone}",
                    "label": display,
                    "target_phone": phone,
                    "network": str(network).strip().upper() if network else None,
                    "recipient_name": data.get("recipient_name") or item.get("label"),
                }
            )
    return candidates


def _ambiguous_phone_referent_prompt(candidates: list[dict[str, Any]], locale: str) -> str | None:
    lines = [f"{idx}. {str(candidate.get('label') or f'Option {idx}')}" for idx, candidate in enumerate(candidates, start=1)]
    if not lines:
        return None
    prompt = render_message("referent.phone.which_number", locale)
    reply_hint = render_message("query.clarify.reply_number_or_rephrase", locale)
    return f"{prompt}\n" + "\n".join(lines) + f"\n{reply_hint}"


def _resolve_referent_phone_selection_from_input(
    user_message: str,
    existing_candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None]:
    clean_msg = user_message.strip()
    if not clean_msg or not existing_candidates:
        return None, None

    selected: dict[str, Any] | None = None
    if clean_msg.isdigit():
        selected_index = int(clean_msg)
        for candidate in existing_candidates:
            try:
                candidate_index = int(candidate.get("index", 0) or 0)
            except (TypeError, ValueError):
                continue
            if candidate_index == selected_index:
                selected = candidate
                break
        if selected is None:
            return None, existing_candidates
    else:
        normalized_input = clean_msg.lower()
        for candidate in existing_candidates:
            option_id = str(candidate.get("option_id") or "").strip().lower()
            if option_id and normalized_input == option_id:
                selected = candidate
                break
        if selected is None:
            return None, None

    patch = {
        "target_phone": selected.get("target_phone"),
        "referent_phone_candidates": [],
    }
    if selected.get("network"):
        patch["network"] = selected["network"]
    return patch, None


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
        waiting_for_referent_phone = "referent_phone_id" in required_fields
        waiting_for_target_phone = bool({"target_phone", "recipient_phone", "phone"} & set(required_fields))
        waiting_for_network = set(required_fields) == {"network"}
        if waiting_for_target_phone:
            self_phone = _self_target_phone(self.user_message, context)
            normalized_phone = self_phone or _first_normalized_phone(self.user_message)
            if normalized_phone:
                payload.target_phone = normalized_phone
                payload.is_self = bool(self_phone)
                payload.skip_extraction = False
                payload.stage = "extracted"
                return None
        if waiting_for_network:
            network = _network_reply(self.user_message)
            if network:
                payload.network = network
                payload.skip_extraction = False
                payload.stage = "extracted"
                return None
        if waiting_for_referent_phone:
            referent_patch, invalid_referents = _resolve_referent_phone_selection_from_input(
                self.user_message,
                payload.referent_phone_candidates,
            )
            if referent_patch:
                for field, value in referent_patch.items():
                    if hasattr(payload, field):
                        setattr(payload, field, value)
                payload.stage = "extracted"
                return None
            if invalid_referents:
                prompt = _ambiguous_phone_referent_prompt(invalid_referents, context.language)
                patch = payload.model_dump(exclude_none=True)
                patch["referent_phone_candidates"] = invalid_referents
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["referent_phone_id"],
                    prompt=prompt,
                    patch=patch,
                    details={
                        "ambiguity": "MULTIPLE_REFERENT_PHONES",
                        "candidates": invalid_referents,
                    },
                )
        if not payload.target_phone:
            phone_candidates = _referent_phone_candidates(context)
            ambiguity_prompt = _ambiguous_phone_referent_prompt(phone_candidates, context.language)
            if ambiguity_prompt:
                patch = payload.model_dump(exclude_none=True)
                patch["referent_phone_candidates"] = phone_candidates
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["referent_phone_id"],
                    prompt=ambiguity_prompt,
                    patch=patch,
                    details={
                        "ambiguity": "MULTIPLE_REFERENT_PHONES",
                        "candidates": phone_candidates,
                    },
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

        referent_applied = _apply_resolved_referents(payload, context)
        if payload.skip_extraction:
            override_reason = _skip_override_reason(payload, self.user_message)
            payload.skip_extraction = False
            if referent_applied:
                logger.info("deterministic_data_referent_fastpath")
                payload.stage = "extracted"
                return None
            if override_reason is None:
                logger.info("skip_redundant_extraction", task="data", reason="no_override_signal")
                return None
            logger.info("override_skip_extraction", task="data", reason=override_reason)

        extractor = worker_context.extractor
        if not extractor:
            logger.info("data_extraction_skipped", reason="extractor_unavailable")
            _apply_resolved_referents(payload, context)
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

        _apply_resolved_referents(payload, context)

        # TODO: Handle 'amount' or 'budget' text to float mapping more robustly if needed
        # For now assuming simple mapping usually happens in resolution or prior

        payload.stage = "extracted"
        return None  # Continue pipeline
