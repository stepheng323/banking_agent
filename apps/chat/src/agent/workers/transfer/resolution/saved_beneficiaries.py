"""Saved-beneficiary and recipient-memory helpers for transfer resolution."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from apps.chat.src.agent.workers.transfer.models.types import TransferContext, TransferPayload
from apps.chat.src.agent.workers.transfer.resolution.names import (
    ask_account_and_bank_prompt,
    beneficiary_provider,
    build_name_consistency_patch,
    optional_text,
    provider_name,
)
from banking.presentation.i18n.renderer import render_message
from shared.database.models import Beneficiary
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def is_transfer_beneficiary_record(record: dict[str, Any]) -> bool:
    """Return whether the record is an explicit transfer beneficiary."""
    beneficiary_type = record.get("beneficiary_type")
    return str(beneficiary_type).strip().lower() == "transfer"


def resolve_beneficiary_from_reference(
    payload: TransferPayload,
    candidates: list[Beneficiary],
) -> Beneficiary | None:
    reference = payload.recipient_reference if isinstance(payload.recipient_reference, dict) else None
    if not reference:
        return None

    selector = str(reference.get("selector") or "").strip().lower()
    if selector == "index":
        raw_index = reference.get("index")
        if raw_index is None:
            return None
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            return None
        if 1 <= index <= len(candidates):
            return candidates[index - 1]
        return None

    return None


def build_single_beneficiary_patch(single: Beneficiary, recipient_name: str | None) -> dict[str, Any]:
    requested_alias = str(recipient_name or "").strip()
    beneficiary_alias = str(single.alias or "").strip()
    account_name = str(single.account_name or "").strip()
    resolved_name = account_name or beneficiary_alias or requested_alias or None
    alias_name = requested_alias or beneficiary_alias or account_name or None
    bank_code = optional_text(single.bank_code)
    provider = beneficiary_provider(None, bank_code)

    return {
        "recipient_account": optional_text(single.account_number),
        "recipient_bank_code": bank_code,
        "recipient_bank_name": single.bank_name,
        "recipient_bank_code_provider": provider,
        "recipient_resolution_provider": provider,
        "recipient_name": alias_name,
        "recipient_resolved_name": resolved_name,
        "beneficiary_id": str(single.id),
        "resolved_from_saved_beneficiary": True,
        "name_mismatch": False,
        "name_match_score": None,
        "name_mismatch_warning": None,
        "beneficiary_candidates": [],
    }


def build_single_beneficiary_patch_from_record(record: dict[str, Any], recipient_name: str | None) -> dict[str, Any]:
    requested_alias = str(recipient_name or "").strip()
    beneficiary_alias = str(record.get("alias") or "").strip()
    account_name = str(record.get("account_name") or "").strip()
    resolved_name = account_name or beneficiary_alias or requested_alias or None
    alias_name = requested_alias or beneficiary_alias or account_name or None
    beneficiary_id = str(record.get("id") or "").strip() or None
    bank_code = optional_text(record.get("bank_code"))
    provider = beneficiary_provider(record.get("bank_code_provider"), bank_code)
    resolution_provider = beneficiary_provider(record.get("resolution_provider"), bank_code)

    return {
        "recipient_account": optional_text(record.get("account_number")),
        "recipient_bank_code": bank_code,
        "recipient_bank_name": record.get("bank_name"),
        "recipient_bank_code_provider": provider,
        "recipient_resolution_provider": resolution_provider or provider,
        "recipient_name": alias_name,
        "recipient_resolved_name": resolved_name,
        "beneficiary_id": beneficiary_id,
        "resolved_from_saved_beneficiary": beneficiary_id is not None,
        "name_mismatch": False,
        "name_match_score": None,
        "name_mismatch_warning": None,
        "beneficiary_candidates": [],
    }


async def saved_beneficiary_result(
    patch: dict[str, Any],
    payload: TransferPayload,
    locale: str,
    resolver_provider: Any | None,
    bank_cache: Any | None,
) -> TransactionResult:
    """Return a saved-beneficiary patch, resolving Mono bank code when the saved record has only a bank name."""
    if patch.get("recipient_bank_code"):
        return TransactionResult(outcome=TransactionOutcome.OK, patch=patch)

    account_number = optional_text(patch.get("recipient_account"))
    bank_name = optional_text(patch.get("recipient_bank_name"))
    if not account_number or not bank_name or not resolver_provider or not bank_cache:
        return TransactionResult(outcome=TransactionOutcome.OK, patch=patch)

    try:
        await bank_cache.ensure_banks_cached(resolver_provider.get_banks)
        bank_code = await bank_cache.get_bank_code(bank_name)
    except Exception as exc:
        logger.warning("saved_beneficiary_bank_lookup_failed", error=str(exc))
        bank_code = None

    if not bank_code:
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=["recipient_bank_name"],
            prompt=render_message("transfer.resolve.bank_name_not_found", locale, {"bank_name": bank_name}),
        )

    resolver_name = provider_name(resolver_provider)
    try:
        resolved = await resolver_provider.resolve_account(account_number, bank_code)
    except Exception as exc:
        logger.warning("saved_beneficiary_account_resolution_failed", error=str(exc), provider=resolver_name)
        resolved = None

    if not resolved or not resolved.success or not resolved.account:
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=["recipient_account", "recipient_bank_name"],
            prompt=(
                f"{render_message('response.templates.account_validation_failed', locale)} "
                f"{ask_account_and_bank_prompt(locale, patch.get('recipient_name'))}"
            ),
        )

    resolved_name = resolved.account.account_name
    patch.update(
        {
            "recipient_resolved_name": resolved_name or patch.get("recipient_resolved_name"),
            "recipient_bank_code": resolved.account.bank_code or bank_code,
            "recipient_bank_code_provider": resolver_name,
            "recipient_resolution_provider": resolver_name,
        }
    )
    if not patch.get("recipient_name") and resolved_name:
        patch["recipient_name"] = resolved_name

    consistency_payload = payload.model_copy(
        update={
            "recipient_name": patch.get("recipient_name"),
            "recipient_account": account_number,
            "recipient_bank_code": patch.get("recipient_bank_code"),
            "recipient_bank_name": bank_name,
        }
    )
    patch.update(build_name_consistency_patch(consistency_payload, resolved_name, locale))
    return TransactionResult(outcome=TransactionOutcome.OK, patch=patch)


def build_beneficiary_clarify_result(
    recipient_name: str | None,
    candidates: list[Beneficiary],
    locale: str,
) -> TransactionResult:
    candidate_list = []
    options = []
    for idx, candidate in enumerate(candidates, start=1):
        beneficiary_id = str(candidate.id)
        option_id = f"bene:{beneficiary_id}"
        label = f"{candidate.account_name or candidate.alias} • {candidate.bank_name} • ****{str(candidate.account_number)[-4:]}"
        candidate_list.append(
            {
                "index": idx,
                "beneficiary_id": beneficiary_id,
                "option_id": option_id,
                "label": label,
            }
        )
        options.append({"id": option_id, "title": label})

    numbered_lines = [f"{candidate['index']}. {candidate['label']}" for candidate in candidate_list]
    candidates_list = "\n".join(numbered_lines)
    prompt = render_message(
        "response.templates.clarify_beneficiary",
        locale,
        {
            "recipient_name": recipient_name or render_message("response.common.recipient_fallback", locale),
            "candidates_list": candidates_list,
        },
    )
    reply_hint = render_message("query.clarify.reply_number_or_rephrase", locale)
    logger.info(
        "beneficiary_ambiguity_prompted",
        recipient_name=recipient_name,
        candidate_count=len(candidate_list),
    )
    return TransactionResult(
        outcome=TransactionOutcome.NEEDS_INPUT,
        required_fields=["beneficiary_id"],
        prompt=f"{prompt}\n{reply_hint}",
        patch={"beneficiary_candidates": candidate_list},
        details={
            "ambiguity": "MULTIPLE_BENEFICIARIES",
            "candidates": candidate_list,
            "options": options,
        },
    )


def referent_item_data(item: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    data = item.get("data")
    return data if isinstance(data, dict) else {}


def beneficiary_record_from_referent_item(item: dict[str, Any]) -> dict[str, Any]:
    data = referent_item_data(item)
    return {
        "id": data.get("beneficiary_id") or data.get("id") or item.get("entity_id"),
        "alias": data.get("alias") or data.get("recipient_name") or item.get("label"),
        "account_name": data.get("account_name") or data.get("recipient_resolved_name") or data.get("recipient_name"),
        "account_number": data.get("account_number") or data.get("recipient_account"),
        "bank_name": data.get("bank_name") or data.get("recipient_bank_name"),
        "bank_code": data.get("bank_code") or data.get("recipient_bank_code"),
        "bank_code_provider": data.get("bank_code_provider") or data.get("recipient_bank_code_provider"),
        "resolution_provider": data.get("resolution_provider") or data.get("recipient_resolution_provider"),
        "beneficiary_type": data.get("beneficiary_type") or "transfer",
    }


def memory_recipient_resolution(ctx: TransferContext) -> dict[str, Any] | None:
    candidate = ctx.resolved_referents.get("recipient")
    return candidate if isinstance(candidate, dict) else None


def build_memory_recipient_clarify_result(resolution: dict[str, Any], locale: str) -> TransactionResult:
    raw_candidates = resolution.get("candidates")
    candidates = raw_candidates if isinstance(raw_candidates, list) else []
    candidate_list = []
    options = []
    for idx, item in enumerate(candidates[:5], start=1):
        if not isinstance(item, dict):
            continue
        data = referent_item_data(item)
        account = str(data.get("account_number") or data.get("recipient_account") or "").strip()
        bank = str(data.get("bank_name") or data.get("recipient_bank_name") or "").strip()
        label = str(item.get("label") or data.get("recipient_name") or data.get("alias") or "Recipient").strip()
        if bank or account:
            suffix = " • ".join(part for part in (bank, f"****{account[-4:]}" if account else "") if part)
            label = f"{label} • {suffix}"
        option_id = f"referent:{idx}"
        record = beneficiary_record_from_referent_item(item)
        candidate_list.append(
            {
                "index": idx,
                "option_id": option_id,
                "label": label,
                "beneficiary_id": record.get("id"),
                "recipient_name": record.get("alias"),
                "recipient_resolved_name": record.get("account_name"),
                "recipient_account": record.get("account_number"),
                "recipient_bank_name": record.get("bank_name"),
                "recipient_bank_code": record.get("bank_code"),
                "recipient_bank_code_provider": record.get("bank_code_provider"),
                "recipient_resolution_provider": record.get("resolution_provider"),
                "resolved_from_saved_beneficiary": bool(record.get("id")),
            }
        )
        options.append({"id": option_id, "title": label})

    lines = "\n".join(f"{candidate['index']}. {candidate['label']}" for candidate in candidate_list)
    prompt = render_message("transfer.resolve.which_recipient", locale, fallback_en="Which recipient did you mean?")
    if lines:
        prompt = f"{prompt}\n{lines}"
    return TransactionResult(
        outcome=TransactionOutcome.NEEDS_INPUT,
        required_fields=["referent_recipient_id"],
        prompt=prompt,
        patch={"referent_recipient_candidates": candidate_list},
        details={"ambiguity": "MULTIPLE_REFERENT_RECIPIENTS", "candidates": candidate_list, "options": options},
    )


async def resolve_memory_recipient_result(
    payload: TransferPayload,
    ctx: TransferContext,
    locale: str,
    resolver_provider: Any | None,
    bank_cache: Any | None,
) -> TransactionResult | None:
    resolution = memory_recipient_resolution(ctx)
    if not resolution:
        return None
    status = str(resolution.get("status") or "").strip().lower()
    if status == "ambiguous":
        return build_memory_recipient_clarify_result(resolution, locale)
    if status != "resolved":
        return None
    item = resolution.get("item")
    if not isinstance(item, dict):
        return None
    record = beneficiary_record_from_referent_item(item)
    if not (record.get("account_number") or record.get("alias") or record.get("account_name")):
        return None
    return await saved_beneficiary_result(
        build_single_beneficiary_patch_from_record(record, None),
        payload,
        locale,
        resolver_provider,
        bank_cache,
    )
