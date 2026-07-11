"""Selection helpers for transfer extraction follow-up slots."""

from typing import Any

from banking.beneficiaries.services.matcher import BeneficiaryMatcher
from banking.beneficiaries.services.selection import match_beneficiary_candidate_selection
from banking.presentation.i18n.renderer import render_message
from banking.transactions.shared.account_selection.reference import build_source_account_patch
from banking.transfers.models.types import TransferContext, TransferPayload
from banking.transfers.resolution.saved_beneficiaries import build_single_beneficiary_patch_from_record
from shared.database.models import Beneficiary
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_SELECTED_CANDIDATE_PATCH_FIELDS = (
    "recipient_name",
    "recipient_resolved_name",
    "recipient_account",
    "recipient_bank_name",
    "recipient_bank_code",
    "recipient_bank_code_provider",
    "recipient_resolution_provider",
    "recipient_resolution_mode",
    "resolved_from_saved_beneficiary",
)


def _canonical_beneficiary_id(value: str) -> str:
    text = value.strip()
    if text.startswith("bene:"):
        return text.split(":", 1)[1].strip()
    return text


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _has_recipient_details(patch: dict[str, Any]) -> bool:
    return bool(
        _present(patch.get("recipient_account"))
        and (_present(patch.get("recipient_bank_name")) or _present(patch.get("recipient_bank_code")))
    )


def _patch_from_selected_candidate(candidate: dict[str, Any] | None) -> dict[str, Any]:
    if not candidate:
        return {}
    patch = {
        field: candidate.get(field)
        for field in _SELECTED_CANDIDATE_PATCH_FIELDS
        if candidate.get(field) not in (None, "")
    }
    beneficiary_id = str(candidate.get("beneficiary_id", "")).strip()
    if beneficiary_id:
        patch["beneficiary_id"] = beneficiary_id
    return patch


def _fill_missing_patch_values(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if not _present(target.get(key)) and _present(value):
            target[key] = value


def render_beneficiary_retry_prompt(
    *,
    recipient_name: str | None,
    candidates: list[dict[str, Any]],
    locale: str,
) -> str:
    numbered_lines = [
        f"{idx}. {str(candidate.get('body_label') or candidate.get('label') or f'Option {idx}')}"
        for idx, candidate in enumerate(candidates, start=1)
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


def resolve_beneficiary_selection_from_input(
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
                    "label": (
                        f"{candidate.account_name or candidate.alias} · {candidate.bank_name} · "
                        f"···{str(candidate.account_number)[-4:]}"
                    ),
                }
                for idx, candidate in enumerate(matched_candidates, start=1)
            ]

    if not candidates:
        return None, None

    selected_beneficiary_id: str | None = None
    selected_candidate: dict[str, Any] | None = None
    if clean_msg.isdigit():
        selected_index = int(clean_msg)
        for candidate in candidates:
            if int(candidate.get("index", 0)) == selected_index:
                selected_beneficiary_id = str(candidate.get("beneficiary_id", "")).strip() or None
                selected_candidate = candidate
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
                selected_candidate = candidate
                break
            if canonical_input and canonical_input == beneficiary_id:
                selected_beneficiary_id = beneficiary_id
                selected_candidate = candidate
                break
        if not selected_beneficiary_id:
            selected_beneficiary_id = match_beneficiary_candidate_selection(clean_msg, candidates)
            selected_candidate = next(
                (
                    candidate
                    for candidate in candidates
                    if str(candidate.get("beneficiary_id", "")).strip() == selected_beneficiary_id
                ),
                None,
            )
        if not selected_beneficiary_id:
            return None, None

    selected_record = next(
        (record for record in beneficiaries_raw if str(record.get("id") or "").strip() == selected_beneficiary_id),
        None,
    )
    candidate_patch = _patch_from_selected_candidate(selected_candidate)
    patch: dict[str, Any]
    hydration_source = "id_only"
    if selected_record:
        patch = build_single_beneficiary_patch_from_record(selected_record, None)
        hydration_source = "context_record"
        if not _has_recipient_details(patch) and _has_recipient_details(candidate_patch):
            _fill_missing_patch_values(patch, candidate_patch)
            hydration_source = "context_record_with_candidate_fallback"
    else:
        patch = candidate_patch
        if _has_recipient_details(patch):
            hydration_source = "candidate_payload"
        patch["beneficiary_id"] = selected_beneficiary_id
        patch["beneficiary_candidates"] = []

    logger.info(
        "transfer_extraction_beneficiary_selection",
        beneficiary_id=selected_beneficiary_id,
        hydration_source=hydration_source,
        has_recipient_account=_present(patch.get("recipient_account")),
        has_recipient_bank=_present(patch.get("recipient_bank_name")),
    )
    patch["beneficiary_id"] = selected_beneficiary_id
    patch["beneficiary_candidates"] = []
    patch["confirmation"] = {"confirmed": False}
    return (
        patch,
        None,
    )


def render_referent_recipient_retry_prompt(candidates: list[dict[str, Any]], locale: str) -> str:
    prompt = render_message("transfer.resolve.which_recipient", locale, fallback_en="Which recipient did you mean?")
    lines = "\n".join(
        f"{idx}. {str(candidate.get('label') or f'Option {idx}')}" for idx, candidate in enumerate(candidates, start=1)
    )
    reply_hint = render_message("query.clarify.reply_number_or_rephrase", locale)
    return f"{prompt}\n{lines}\n{reply_hint}" if lines else f"{prompt}\n{reply_hint}"


def resolve_referent_recipient_selection_from_input(
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

    patch_fields = (
        "beneficiary_id",
        "recipient_name",
        "recipient_resolved_name",
        "recipient_account",
        "recipient_bank_name",
        "recipient_bank_code",
        "recipient_bank_code_provider",
        "recipient_resolution_provider",
        "resolved_from_saved_beneficiary",
    )
    patch = {field: selected.get(field) for field in patch_fields if selected.get(field) not in (None, "")}
    patch["referent_recipient_candidates"] = []
    patch["confirmation"] = {"confirmed": False}
    return patch, None


def _resolved_referent_data(context: TransferContext, referent_type: str) -> dict[str, Any] | None:
    resolution = context.resolved_referents.get(referent_type)
    if not isinstance(resolution, dict) or resolution.get("status") != "resolved":
        return None
    item = resolution.get("item")
    if not isinstance(item, dict):
        return None
    data = item.get("data")
    return data if isinstance(data, dict) else None


def _resolved_amount_referent(context: TransferContext) -> float | None:
    data = _resolved_referent_data(context, "amount")
    if not data:
        return None
    raw_amount = data.get("amount")
    if raw_amount is None:
        return None
    try:
        amount = float(raw_amount)
    except (TypeError, ValueError):
        return None
    return amount if amount > 0 else None


def build_resolved_referent_patch(data: TransferPayload, context: TransferContext) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    amount = _resolved_amount_referent(context)
    if amount is not None and data.amount is None and not data.transfer_all and data.transfer_percentage is None:
        patch.update(
            {
                "amount": amount,
                "suggested_amount": None,
                "transfer_percentage": None,
                "transfer_all": False,
            }
        )

    if not (data.source_account_id or data.source_bank_name or data.source_account_number or data.source_account_index):
        source_account = _resolved_referent_data(context, "source_account")
        if source_account:
            source_patch = build_source_account_patch(source_account)
            if any(
                source_patch.get(field) for field in ("source_account_id", "source_bank_name", "source_account_number")
            ):
                patch.update(source_patch)

    if patch:
        patch["confirmation"] = {"confirmed": False}
    return patch
