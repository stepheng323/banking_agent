"""Extraction logic."""

import re
from typing import Any

from apps.core.src.agent.graphs.__shared__.beneficiary.matcher import BeneficiaryMatcher
from apps.core.src.agent.graphs.__shared__.extraction_utils import try_extract_numeric_index
from apps.core.src.agent.graphs.__shared__.source_account_guard import find_account_by_bank_name
from apps.core.src.agent.graphs.transfer.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from apps.core.src.agent.graphs.transfer.pipeline.base import TransferStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.database.models import Beneficiary
from shared.i18n import render_message
from shared.services.affirmation.service import AffirmationService
from shared.utils.logging import get_logger
from shared.utils.sanitize import normalize_bank_account_number

logger = get_logger(__name__)

_ACCOUNT_BANK_ACCOUNT_FIRST_PATTERN = re.compile(r"^\s*(?P<account>(?:\d[\s,.\-]?){10,11})\s+(?P<bank>.+?)\s*$")
_ACCOUNT_BANK_BANK_FIRST_PATTERN = re.compile(r"^\s*(?P<bank>.+?)\s+(?P<account>(?:\d[\s,.\-]?){10,11})\s*$")
_AMOUNT_REPLY_PATTERN = re.compile(
    r"^\s*(?:₦|ngn)?\s*(?P<amount>\d[\d,]*(?:\.\d+)?)\s*(?P<suffix>[kKhH]?)\s*$",
    re.IGNORECASE,
)
_SIMPLE_TRANSFER_PREFIX_RE = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|please|pls|abeg|oya|jowo|biko|kindly)\s+)*"
    r"(?P<verb>send|transfer|pay|remit)\s+"
    r"(?P<amount>(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKhH]?)\s+"
    r"(?:to|for)\s+"
    r"(?P<target>.+?)\s*$",
    re.IGNORECASE,
)
_SIMPLE_TRANSFER_COMPLEX_MARKERS_RE = re.compile(
    r"\b(?:each|split|between|btw|half|quarter|tithe|all|everything|from|using|use|with|"
    r"tomorrow|next week|weekly|monthly|every|abroad|international)\b",
    re.IGNORECASE,
)
_SIMPLE_TRANSFER_MULTI_TARGET_RE = re.compile(r"\s(?:and|&)\s|,", re.IGNORECASE)
_CONFIRMATION_EDIT_PREFIX_RE = re.compile(
    r"^(?:(?:make|change|update)\s+(?:it|amount)\s*(?:to\s*)?|"
    r"(?:make|change|update)\s+to\s+|"
    r"send\s+)?",
    re.IGNORECASE,
)
_CONFIRMATION_PERCENTAGE_RE = re.compile(r"^(?P<pct>\d{1,3}(?:\.\d+)?)\s*%$", re.IGNORECASE)
_CONFIRMATION_BANK_SWITCH_RE = re.compile(
    r"^(?:(?:use|switch(?:\s+to)?|change(?:\s+to)?)\s+)?[a-z0-9&' ]+(?:\s+bank)?(?:\s+instead)?$",
    re.IGNORECASE,
)


def _canonical_beneficiary_id(value: str) -> str:
    text = value.strip()
    if text.startswith("bene:"):
        return text.split(":", 1)[1].strip()
    return text


def _render_beneficiary_retry_prompt(
    *,
    recipient_name: str | None,
    candidates: list[dict[str, Any]],
    locale: str,
) -> str:
    numbered_lines = [
        f"{idx}. {str(candidate.get('label') or f'Option {idx}')}" for idx, candidate in enumerate(candidates, start=1)
    ]
    candidates_list = "\n".join(numbered_lines)
    prompt = render_message(
        "response.templates.clarify_beneficiary",
        locale,
        {
            "recipient_name": recipient_name or "",
            "candidates_list": candidates_list,
        },
    )
    reply_hint = render_message("query.clarify.reply_number_or_rephrase", locale)
    return f"{prompt}\n{reply_hint}"


def _resolve_beneficiary_selection_from_input(
    user_message: str,
    existing_candidates: list[dict[str, Any]],
    recipient_name: str | None,
    beneficiaries_raw: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None]:
    clean_msg = user_message.strip()
    if not clean_msg:
        return None, None

    candidates = existing_candidates
    if not candidates and recipient_name and beneficiaries_raw:
        beneficiaries = [Beneficiary(**b) for b in beneficiaries_raw]
        status, _single, matched_candidates = BeneficiaryMatcher().match(recipient_name, beneficiaries)
        if status == "clarify" and matched_candidates:
            candidates = [
                {
                    "index": idx,
                    "beneficiary_id": str(candidate.id),
                    "option_id": f"bene:{candidate.id}",
                    "label": f"{candidate.account_name or candidate.alias} • {candidate.bank_name} • ****{str(candidate.account_number)[-4:]}",
                }
                for idx, candidate in enumerate(matched_candidates, start=1)
            ]

    if not candidates:
        return None, None

    selected_beneficiary_id: str | None = None
    if clean_msg.isdigit():
        selected_index = int(clean_msg)
        for candidate in candidates:
            if int(candidate.get("index", 0)) == selected_index:
                selected_beneficiary_id = str(candidate.get("beneficiary_id", "")).strip() or None
                break
        if not selected_beneficiary_id:
            return None, candidates
    else:
        normalized_input = clean_msg.lower()
        canonical_input = _canonical_beneficiary_id(clean_msg)
        for candidate in candidates:
            option_id = str(candidate.get("option_id", "")).strip()
            beneficiary_id = str(candidate.get("beneficiary_id", "")).strip()
            if not beneficiary_id:
                continue
            if option_id and normalized_input == option_id.lower():
                selected_beneficiary_id = beneficiary_id
                break
            if canonical_input and canonical_input == beneficiary_id:
                selected_beneficiary_id = beneficiary_id
                break
        if not selected_beneficiary_id:
            return None, None

    logger.info("transfer_extraction_beneficiary_selection", beneficiary_id=selected_beneficiary_id)
    return (
        {
            "beneficiary_id": selected_beneficiary_id,
            "beneficiary_candidates": [],
            "confirmation": {"confirmed": False},
        },
        None,
    )


def _should_override_skip_extraction(payload: TransferPayload) -> bool:
    """Return True when planner-provided recipient text still needs LLM entity extraction."""
    if payload.recipient_account and (payload.recipient_bank_name or payload.recipient_bank_code):
        return False

    recipient_hint = (payload.recipient_name or "").strip()
    if not recipient_hint:
        return False

    digits_only = "".join(ch for ch in recipient_hint if ch.isdigit())
    return len(digits_only) >= 10


def _parse_account_and_bank_input(user_message: str) -> tuple[str, str] | None:
    """Parse account+bank in either order, normalizing account separators."""
    text = user_message.strip()
    if not text:
        return None

    for pattern in (_ACCOUNT_BANK_ACCOUNT_FIRST_PATTERN, _ACCOUNT_BANK_BANK_FIRST_PATTERN):
        match = pattern.match(text)
        if not match:
            continue

        normalized_account = normalize_bank_account_number(match.group("account"))
        bank_name = match.group("bank").strip().strip(",.- ")

        if len(normalized_account) != 10:
            continue
        if not bank_name or bank_name.isdigit():
            continue

        return normalized_account, bank_name

    return None


def _parse_amount_input(user_message: str) -> float | None:
    """Parse shorthand amount replies like '20k', '20000', '₦20,000'."""
    match = _AMOUNT_REPLY_PATTERN.match(user_message.strip())
    if not match:
        return None

    try:
        value = float(match.group("amount").replace(",", ""))
    except ValueError:
        return None

    suffix = match.group("suffix").lower()
    multiplier = 1000.0 if suffix == "k" else 100.0 if suffix == "h" else 1.0
    amount = value * multiplier
    if amount <= 0:
        return None
    return amount


def _parse_simple_transfer_command(
    user_message: str,
    current_payload: TransferPayload,
) -> dict[str, Any] | None:
    """Deterministically parse obvious single-recipient send commands.

    This is intentionally narrow and only used to avoid an LLM hop on fresh,
    simple transfers such as "send 5k to mum". Anything batch-like, scheduled,
    account-aware, or source-qualified falls back to the extractor.
    """
    if any(
        (
            current_payload.amount is not None,
            current_payload.transfer_percentage is not None,
            current_payload.transfer_all,
            current_payload.recipient_name,
            current_payload.recipient_account,
            current_payload.recipient_bank_name,
            current_payload.source_bank_name,
            current_payload.source_accounts,
            current_payload.use_dual_accounts is not None,
            current_payload.explicit_split,
        )
    ):
        return None

    normalized = re.sub(r"\s+", " ", user_message.strip())
    if not normalized:
        return None
    if _SIMPLE_TRANSFER_COMPLEX_MARKERS_RE.search(normalized):
        return None

    match = _SIMPLE_TRANSFER_PREFIX_RE.match(normalized)
    if not match:
        return None

    target = match.group("target").strip().strip(".!?")
    if not target or _SIMPLE_TRANSFER_MULTI_TARGET_RE.search(target):
        return None

    amount = _parse_amount_input(match.group("amount"))
    if amount is None or amount < 1000:
        return None

    patch: dict[str, Any] = {
        "amount": float(amount),
        "confirmation": {"confirmed": False},
        "suggested_amount": None,
        "transfer_percentage": None,
        "transfer_all": False,
    }

    account_and_bank = _parse_account_and_bank_input(target)
    if account_and_bank is not None:
        recipient_account, recipient_bank_name = account_and_bank
        patch.update(
            {
                "recipient_account": recipient_account,
                "recipient_bank_name": recipient_bank_name,
                "recipient_bank_code": None,
                "recipient_resolved_name": None,
                "name_mismatch": False,
                "name_match_score": None,
                "name_mismatch_warning": None,
            }
        )
        return patch

    normalized_account = normalize_bank_account_number(target)
    if len(normalized_account) == 10:
        patch["recipient_account"] = normalized_account
        return patch

    if target.isdigit():
        return None

    patch["recipient_name"] = target
    return patch


def _normalize_name_token(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _recipient_name_matches_existing_binding(new_name: str | None, current_payload: TransferPayload) -> bool:
    normalized_new = _normalize_name_token(new_name)
    if not normalized_new:
        return False

    normalized_known = {
        _normalize_name_token(current_payload.recipient_name),
        _normalize_name_token(current_payload.recipient_resolved_name),
    }
    normalized_known.discard("")
    if not normalized_known:
        return False

    if normalized_new in normalized_known:
        return True

    new_tokens = set(normalized_new.split())
    if not new_tokens:
        return False

    for known in normalized_known:
        known_tokens = set(known.split())
        if not known_tokens:
            continue
        if known_tokens.issubset(new_tokens) or new_tokens.issubset(known_tokens):
            return True

    return False


def _normalize_user_message(value: str) -> str:
    compact = re.sub(r"\s+", " ", value.strip().lower())
    return compact.strip('.,!?;:"`~()[]{}')


def _format_amount_ack(amount: float) -> str:
    rounded = float(amount)
    if rounded.is_integer():
        integer_amount = int(rounded)
        if integer_amount >= 1000 and integer_amount % 1000 == 0:
            return f"Changing amount to {integer_amount // 1000}k."
        return f"Changing amount to ₦{integer_amount:,}."
    return f"Changing amount to ₦{rounded:,.2f}."


def _build_confirmation_edit_description(current_payload: TransferPayload) -> str | None:
    name = current_payload.recipient_name or current_payload.recipient_resolved_name
    if not name:
        return None
    return f"Transfer to {str(name).title()}"


def _parse_single_confirmation_amount_edit(
    user_message: str,
    current_payload: TransferPayload,
) -> dict[str, Any] | None:
    normalized = _normalize_user_message(user_message)
    if not normalized:
        return None

    candidate = _CONFIRMATION_EDIT_PREFIX_RE.sub("", normalized, count=1).strip() or normalized
    patch: dict[str, Any] | None = None

    parsed_amount = _parse_amount_input(candidate)
    if parsed_amount is not None:
        patch = {
            "amount": parsed_amount,
            "transfer_percentage": None,
            "transfer_all": False,
            "funding_plan": None,
            "suggested_amount": None,
            "confirmation": {"confirmed": False},
            "transition_acknowledgment": _format_amount_ack(parsed_amount),
        }
    elif candidate in {"all", "everything"}:
        patch = {
            "amount": None,
            "transfer_percentage": None,
            "transfer_all": True,
            "funding_plan": None,
            "suggested_amount": None,
            "confirmation": {"confirmed": False},
            "transition_acknowledgment": "Sending all available funds.",
        }
    elif candidate == "half":
        patch = {
            "amount": None,
            "transfer_percentage": 50.0,
            "transfer_all": False,
            "funding_plan": None,
            "suggested_amount": None,
            "confirmation": {"confirmed": False},
            "transition_acknowledgment": "Changing transfer to half of the available balance.",
        }
    else:
        pct_match = _CONFIRMATION_PERCENTAGE_RE.fullmatch(candidate)
        if pct_match:
            pct_value = float(pct_match.group("pct"))
            if 0 < pct_value <= 100:
                patch = {
                    "amount": None,
                    "transfer_percentage": pct_value,
                    "transfer_all": False,
                    "funding_plan": None,
                    "suggested_amount": None,
                    "confirmation": {"confirmed": False},
                    "transition_acknowledgment": f"Changing transfer to {pct_value:g}% of the available balance.",
                }

    if patch is None:
        return None

    description = _build_confirmation_edit_description(current_payload)
    if description:
        patch["description"] = description
    return patch


def _parse_single_confirmation_source_bank_edit(
    user_message: str,
    current_payload: TransferPayload,
    accounts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    normalized = _normalize_user_message(user_message)
    if not normalized or not _CONFIRMATION_BANK_SWITCH_RE.fullmatch(normalized):
        return None

    candidate = re.sub(r"^(?:use|switch(?:\s+to)?|change(?:\s+to)?)\s+", "", normalized, flags=re.IGNORECASE)
    candidate = re.sub(r"\s+instead$", "", candidate, flags=re.IGNORECASE).strip()
    if not candidate:
        return None

    matched_account = find_account_by_bank_name(accounts, candidate)
    if not matched_account:
        return None

    bank_name = str(matched_account.get("bank_name") or "").strip()
    if not bank_name:
        return None
    if current_payload.source_bank_name and bank_name.lower() == current_payload.source_bank_name.lower():
        return None

    patch: dict[str, Any] = {
        "source_bank_name": bank_name,
        "source_account_id": None,
        "source_account_name": None,
        "source_account_number": None,
        "source_account_index": None,
        "source_affinity_mode": "explicit",
        "funding_plan": None,
        "suggested_amount": None,
        "confirmation": {"confirmed": False},
        "transition_acknowledgment": f"Using {bank_name} instead.",
    }
    description = _build_confirmation_edit_description(current_payload)
    if description:
        patch["description"] = description
    return patch


def _parse_single_confirmation_transfer_edit(
    user_message: str,
    current_payload: TransferPayload,
    context: TransferContext,
    worker_context: Any,
) -> dict[str, Any] | None:
    confirmation_task_count = getattr(worker_context, "confirmation_task_count", None)
    if confirmation_task_count != 1:
        return None
    if not current_payload.previous_confirmation_snapshot:
        return None

    amount_patch = _parse_single_confirmation_amount_edit(user_message, current_payload)
    if amount_patch is not None:
        return amount_patch

    accounts = context.accounts if isinstance(context.accounts, list) else []
    return _parse_single_confirmation_source_bank_edit(user_message, current_payload, accounts)


class ExtractionStep(TransferStep):
    """Refines transfer data from user message."""

    def __init__(self, user_message: str | None):
        self.user_message = user_message

    async def execute(
        self,
        data: TransferPayload,
        context: TransferContext,
        gates: TransferGates,
        worker_context: Any = None,
    ) -> TransactionResult:
        if not self.user_message:
            return TransactionResult(outcome=TransactionOutcome.OK, patch={})

        def _with_skip_patch(patch: dict[str, Any] | None = None) -> dict[str, Any] | None:
            if not data.skip_extraction:
                return patch
            merged = dict(patch or {})
            merged.setdefault("skip_extraction", False)
            return merged

        raw_required_fields = getattr(worker_context, "required_fields", [])
        required_fields = raw_required_fields if isinstance(raw_required_fields, list) else []
        awaiting_amount = "amount" in required_fields
        if awaiting_amount and data.suggested_amount:
            reply = self.user_message.strip()
            if reply == "1":
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    patch=_with_skip_patch(
                        {
                        "amount": float(data.suggested_amount),
                        "suggested_amount": None,
                        "confirmation": {"confirmed": False},
                        }
                    ),
                )
            if reply == "2":
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    patch=_with_skip_patch(
                        {
                        "suggested_amount": None,
                        "confirmation": {"confirmed": False},
                        }
                    ),
                )
            affirmation = AffirmationService.classify_sync(self.user_message)
            if affirmation.is_approval:
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    patch=_with_skip_patch(
                        {
                        "amount": float(data.suggested_amount),
                        "suggested_amount": None,
                        "confirmation": {"confirmed": False},
                        }
                    ),
                )
            if affirmation.is_rejection:
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    patch=_with_skip_patch(
                        {
                        "suggested_amount": None,
                        "confirmation": {"confirmed": False},
                        }
                    ),
                )
        if awaiting_amount:
            parsed_amount = _parse_amount_input(self.user_message)
            if parsed_amount is not None:
                logger.info("deterministic_amount_fastpath", amount=parsed_amount)
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    patch=_with_skip_patch(
                        {
                            "amount": parsed_amount,
                            "suggested_amount": None,
                            "confirmation": {"confirmed": False},
                        }
                    ),
                )

        confirmation_edit_patch = _parse_single_confirmation_transfer_edit(
            self.user_message,
            data,
            context,
            worker_context,
        )
        if confirmation_edit_patch is not None:
            logger.info(
                "deterministic_confirmation_transfer_edit_fastpath",
                fields=sorted(confirmation_edit_patch.keys()),
            )
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch=_with_skip_patch(confirmation_edit_patch),
            )

        # Optimization: Phase 4 (Planner-as-Extractor)
        # Skip extraction if Planner already did it (signaled by flag)
        override_skip_extraction = data.skip_extraction and _should_override_skip_extraction(data)
        if data.skip_extraction and not override_skip_extraction:
            logger.info("skip_redundant_extraction", task="transfer")
            return TransactionResult(outcome=TransactionOutcome.OK, patch={"skip_extraction": False})
        if override_skip_extraction:
            logger.info("override_skip_extraction_for_account_like_recipient", recipient=data.recipient_name)

        waiting_for_beneficiary = "beneficiary_id" in required_fields
        waiting_for_source_account = "source_account_id" in required_fields
        if waiting_for_beneficiary:
            beneficiary_patch, invalid_candidates = _resolve_beneficiary_selection_from_input(
                self.user_message,
                data.beneficiary_candidates,
                data.recipient_name,
                context.beneficiaries,
            )
            if beneficiary_patch:
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    patch=_with_skip_patch(beneficiary_patch),
                )
            if invalid_candidates:
                retry_prompt = _render_beneficiary_retry_prompt(
                    recipient_name=data.recipient_name,
                    candidates=invalid_candidates,
                    locale=context.language,
                )
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["beneficiary_id"],
                    prompt=retry_prompt,
                    patch=_with_skip_patch({"beneficiary_candidates": invalid_candidates}),
                    details={
                        "ambiguity": "MULTIPLE_BENEFICIARIES",
                        "candidates": invalid_candidates,
                        "options": [
                            {
                                "id": str(candidate.get("option_id", "")).strip(),
                                "title": str(candidate.get("label", "")),
                            }
                            for candidate in invalid_candidates
                            if str(candidate.get("option_id", "")).strip()
                        ],
                    },
                )

        # [DETERMINISTIC FALLBACK] Numeric index selection
        # If user replies with "1" or "2" while selecting source account, map directly.
        numeric_patch = try_extract_numeric_index(self.user_message, "transfer") if waiting_for_source_account else None
        if numeric_patch:
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch=_with_skip_patch(numeric_patch),
            )

        # [DETERMINISTIC FAST-PATH] Account number + bank name
        # When user replies with e.g. "8067892221 Opay" and we're waiting for account+bank,
        # parse deterministically instead of relying on the LLM.
        awaiting_account_and_bank = "recipient_account" in required_fields and "recipient_bank_name" in required_fields
        if awaiting_account_and_bank and self.user_message:
            parsed = _parse_account_and_bank_input(self.user_message)
            if parsed:
                acct, bank = parsed
                logger.info(
                    "deterministic_account_bank_fastpath",
                    account=acct,
                    bank=bank,
                )
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    patch=_with_skip_patch(
                        {
                        "recipient_account": acct,
                        "recipient_bank_name": bank,
                        "recipient_bank_code": None,
                        "recipient_resolved_name": None,
                        "name_mismatch": False,
                        "name_match_score": None,
                        "name_mismatch_warning": None,
                        "confirmation": {"confirmed": False},
                        }
                    ),
                )

        simple_transfer_patch = _parse_simple_transfer_command(self.user_message, data)
        if simple_transfer_patch is not None:
            logger.info(
                "deterministic_simple_transfer_fastpath",
                amount=simple_transfer_patch.get("amount"),
                has_recipient_name=bool(simple_transfer_patch.get("recipient_name")),
                has_recipient_account=bool(simple_transfer_patch.get("recipient_account")),
            )
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch=_with_skip_patch(simple_transfer_patch),
            )

        if not worker_context.extractor:
            logger.info("transfer_extraction_skipped", reason="extractor_unavailable")
            patch = _with_skip_patch({})
            return TransactionResult(outcome=TransactionOutcome.OK, patch=patch)

        res = await _extract_transfer_update(
            data,
            worker_context.extractor,
            self.user_message,
            {
                "phone_number": context.phone_number,
                "language": context.language,
                "beneficiaries": context.beneficiaries,
                "accounts": context.accounts,
                "required_fields": worker_context.required_fields,
                "previousResponse": worker_context.previous_response,
                "known_recipient": {
                    "recipient_name": data.recipient_name,
                    "recipient_resolved_name": data.recipient_resolved_name,
                    "recipient_account": data.recipient_account,
                    "recipient_bank_name": data.recipient_bank_name,
                },
            },
        )
        if data.skip_extraction:
            res.patch = _with_skip_patch(res.patch)

        return res


async def _extract_transfer_update(
    current_payload: TransferPayload,
    extractor: Any,
    user_message: str,
    context: dict[str, Any],
) -> TransactionResult:
    """Extract transfer details from user message and merge with current payload."""
    try:
        extraction = await extractor.extract(user_message, smart_context=context)

        extracted_data = {}
        if extraction.entities:
            extracted_data = extraction.entities.model_dump(exclude_unset=True, exclude_none=True)

        if extraction.correction:
            field = extraction.correction.field.value
            value = extraction.correction.new_value
            if field == "bank_name":
                extracted_data["recipient_bank_name"] = value
            else:
                extracted_data[field] = value

            logger.info("transfer_extraction_correction_applied", field=field)

        if not extracted_data and not extraction.acknowledgment:
            return TransactionResult(outcome=TransactionOutcome.OK)

        extracted_percentage = "transfer_percentage" in extracted_data
        extracted_transfer_all = extracted_data.get("transfer_all") is True

        if extracted_data:
            extracted_data["confirmation"] = {"confirmed": False}
            if "amount" in extracted_data:
                extracted_data["suggested_amount"] = None
                extracted_data["transfer_percentage"] = None
                extracted_data["transfer_all"] = False
            if extracted_percentage or extracted_transfer_all:
                extracted_data["amount"] = None
                extracted_data["suggested_amount"] = None

        # [UX] Narration vs Description Split
        # Default description to "Transfer to {name}"
        # user_note only populated if user actually typed one.
        name = (
            extracted_data.get("recipient_resolved_name")
            or extracted_data.get("recipient_name")
            or current_payload.recipient_resolved_name
            or current_payload.recipient_name
        )
        if name:
            extracted_data["description"] = f"Transfer to {name.title()}"

        if "narration" in extracted_data:
            # Keep the execution-facing narration while also preserving a user-authored note
            # for confirmation summaries.
            extracted_data["user_note"] = extracted_data["narration"]

        needs_source = (
            current_payload.recipient_account
            and current_payload.recipient_bank_name
            and not current_payload.source_account_id
        )

        if "bank_name" in extracted_data:
            if needs_source:
                extracted_data["source_bank_name"] = extracted_data.pop("bank_name")
            else:
                extracted_data["recipient_bank_name"] = extracted_data.pop("bank_name")

        if "bank_code" in extracted_data:
            extracted_data["recipient_bank_code"] = extracted_data.pop("bank_code")

        if extraction.acknowledgment:
            extracted_data["transition_acknowledgment"] = extraction.acknowledgment

        if "recipient_bank_name" in extracted_data:
            extracted_data["recipient_bank_code"] = None
            extracted_data["recipient_resolved_name"] = None
            extracted_data["name_mismatch"] = False
            extracted_data["name_match_score"] = None
            extracted_data["name_mismatch_warning"] = None

        if "source_bank_name" in extracted_data:
            extracted_data["source_account_id"] = None
            extracted_data["source_account_name"] = None
            extracted_data["source_account_number"] = None

        if "source_account_index" in extracted_data:
            extracted_data["source_account_id"] = None
            extracted_data["source_bank_name"] = None
            extracted_data["source_account_name"] = None
            extracted_data["source_account_number"] = None

        if (
            "source_accounts" in extracted_data
            or "use_dual_accounts" in extracted_data
            or "explicit_split" in extracted_data
        ):
            extracted_data["source_account_id"] = None
            extracted_data["source_bank_name"] = None
            extracted_data["source_account_name"] = None
            extracted_data["source_account_number"] = None

        explicit_source_fields = {
            "source_bank_name",
            "source_account_index",
            "source_accounts",
            "use_dual_accounts",
            "explicit_split",
        }
        if any(field in extracted_data for field in explicit_source_fields):
            extracted_data["source_affinity_mode"] = "explicit"

        if "recipient_account" in extracted_data:
            extracted_data["recipient_resolved_name"] = None
            extracted_data["name_mismatch"] = False
            extracted_data["name_match_score"] = None
            extracted_data["name_mismatch_warning"] = None

        if "recipient_name" in extracted_data and "recipient_account" not in extracted_data:
            # [FIX] Only clear account details if the name actually changed.
            # This prevents re-extraction (e.g. from proper nouns in synthesized messages)
            # from wiping out valid account details we just collected.
            new_name = extracted_data["recipient_name"]
            names_match = _recipient_name_matches_existing_binding(new_name, current_payload)

            if not names_match:
                extracted_data["recipient_account"] = None
                extracted_data["recipient_bank_code"] = None
                extracted_data["recipient_bank_name"] = None
                extracted_data["recipient_resolved_name"] = None
                extracted_data["beneficiary_id"] = None
                extracted_data["resolved_from_saved_beneficiary"] = False
                extracted_data["name_mismatch"] = False
                extracted_data["name_match_score"] = None
                extracted_data["name_mismatch_warning"] = None
                extracted_data["is_high_risk_transfer"] = False
                extracted_data["dynamic_risk_threshold"] = None
                extracted_data["high_risk_warning"] = None

        funding_invalidation_fields = {
            "amount",
            "source_bank_name",
            "source_account_index",
            "source_account_id",
            "source_accounts",
            "use_dual_accounts",
            "explicit_split",
        }
        if any(field in extracted_data for field in funding_invalidation_fields):
            extracted_data["funding_plan"] = None

        return TransactionResult(outcome=TransactionOutcome.OK, patch=extracted_data)

    except Exception as e:
        logger.error("extraction_node_failed", error=str(e))
        return TransactionResult(outcome=TransactionOutcome.OK)
