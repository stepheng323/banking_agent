"""Beneficiary resolution logic."""

import difflib
import re
import unicodedata
from typing import Any

from apps.chat.src.agent.graphs.__shared__.beneficiary.matcher import BeneficiaryMatcher
from apps.chat.src.agent.graphs.transfer.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from apps.chat.src.agent.graphs.transfer.pipeline.base import TransferStep
from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.database.models import Beneficiary
from shared.formatters.currency import format_naira_compact
from shared.formatters.prompts import sanitize_recipient_display_name
from shared.guardrails.loader import get_cached_guardrails
from shared.i18n import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_RECIPIENT_PRONOUN_TOKENS = {"her", "him", "them", "that", "it", "this", "previous"}
_UNSAFE_RECIPIENT_TOKENS = _RECIPIENT_PRONOUN_TOKENS | {"send", "transfer", "pay", "recipient", "s"}


def _provider_name(provider: Any, default: str = "mono") -> str:
    value = getattr(provider, "provider_name", None)
    return str(value or default).strip().lower()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "none":
        return None
    return text


def _beneficiary_provider(value: Any, bank_code: str | None) -> str | None:
    if not bank_code:
        return None
    provider = _optional_text(value)
    return (provider or "mono").lower()


def _canonical_beneficiary_id(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if text.startswith("bene:"):
        return text.split(":", 1)[1].strip() or None
    return text


def _normalize_name(value: str | None) -> str:
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", without_marks.lower()).strip()


def _recipient_tokens(value: str | None) -> list[str]:
    if not value:
        return []
    lowered = value.strip().lower()
    lowered = re.sub(r"([a-z])['’]s\b", r"\1", lowered)
    normalized = re.sub(r"[^a-z0-9]+", " ", lowered).strip()
    return [token for token in normalized.split() if token]


def _is_pronoun_recipient(value: str | None) -> bool:
    tokens = _recipient_tokens(value)
    return bool(tokens) and all(token in _RECIPIENT_PRONOUN_TOKENS for token in tokens)


def _is_unsafe_recipient_placeholder(value: str | None) -> bool:
    tokens = _recipient_tokens(value)
    return bool(tokens) and all(token in _UNSAFE_RECIPIENT_TOKENS for token in tokens)


def _similarity_score(left: str | None, right: str | None) -> float:
    norm_left = _normalize_name(left)
    norm_right = _normalize_name(right)
    if not norm_left or not norm_right:
        return 1.0

    base_ratio = difflib.SequenceMatcher(a=norm_left, b=norm_right).ratio()
    sorted_left = " ".join(sorted(norm_left.split()))
    sorted_right = " ".join(sorted(norm_right.split()))
    token_ratio = difflib.SequenceMatcher(a=sorted_left, b=sorted_right).ratio()
    return max(base_ratio, token_ratio)


def _is_relational_alias(name: str | None, aliases: list[str]) -> bool:
    normalized = _normalize_name(name)
    if not normalized:
        return False
    normalized_aliases = {_normalize_name(alias) for alias in aliases}
    return normalized in normalized_aliases


def _build_name_consistency_patch(payload: TransferPayload, resolved_name: str | None, locale: str) -> dict[str, Any]:
    if not resolved_name:
        return {"name_mismatch": False, "name_match_score": None, "name_mismatch_warning": None}

    requested_name = (payload.recipient_name or "").strip()
    if not requested_name:
        return {"name_mismatch": False, "name_match_score": None, "name_mismatch_warning": None}
    if _is_unsafe_recipient_placeholder(requested_name):
        return {"name_mismatch": False, "name_match_score": None, "name_mismatch_warning": None}

    # Skip mismatch check if the "name" is actually an account number.
    # When user provides "816 251 1024 zenith", extraction may keep the
    # digits as recipient_name before resolver overwrites it.
    digits_only = "".join(ch for ch in requested_name if ch.isdigit())
    if len(digits_only) >= 8:
        return {"name_mismatch": False, "name_match_score": None, "name_mismatch_warning": None}

    guardrails = get_cached_guardrails()
    aliases = guardrails.transfer.relational_aliases
    if _is_relational_alias(requested_name, aliases):
        return {"name_mismatch": False, "name_match_score": None, "name_mismatch_warning": None}

    score = _similarity_score(requested_name, resolved_name)
    threshold = float(guardrails.transfer.name_match.min_similarity)
    if score < threshold:
        warning = render_message(
            "transfer.confirmation.name_mismatch_warning",
            locale,
            {"requested_name": requested_name, "resolved_name": resolved_name},
        )
        return {
            "name_mismatch": True,
            "name_match_score": round(score, 2),
            "name_mismatch_warning": warning,
        }

    return {
        "name_mismatch": False,
        "name_match_score": round(score, 2),
        "name_mismatch_warning": None,
    }


def _matches_selected_beneficiary(payload: TransferPayload, selected: dict[str, Any]) -> bool:
    """Return True when current payload still targets the selected beneficiary."""
    selected_account = str(selected.get("account_number") or "").strip()
    selected_bank_code = str(selected.get("bank_code") or "").strip()
    selected_bank_code_provider = str(selected.get("bank_code_provider") or "mono").strip().lower()
    selected_bank_name = str(selected.get("bank_name") or "").strip()
    selected_alias = str(selected.get("alias") or "").strip()
    selected_account_name = str(selected.get("account_name") or "").strip()

    req_account = str(payload.recipient_account or "").strip()
    if req_account and selected_account and req_account != selected_account:
        return False

    req_bank_code = str(payload.recipient_bank_code or "").strip()
    req_bank_code_provider = str(payload.recipient_bank_code_provider or selected_bank_code_provider).strip().lower()
    if (
        req_bank_code
        and selected_bank_code
        and req_bank_code_provider == selected_bank_code_provider
        and req_bank_code != selected_bank_code
    ):
        return False

    req_bank_name = str(payload.recipient_bank_name or "").strip()
    if req_bank_name and selected_bank_name and _normalize_name(req_bank_name) != _normalize_name(selected_bank_name):
        return False

    req_name = _normalize_name(payload.recipient_name)
    if req_name:
        candidates = [_normalize_name(selected_alias), _normalize_name(selected_account_name)]
        if not any(req_name and cand and (req_name in cand or cand in req_name) for cand in candidates):
            return False

    return True


def _clear_stale_beneficiary_binding(payload: TransferPayload, selected: dict[str, Any]) -> None:
    """Detach payload from previously selected beneficiary when recipient changes."""
    selected_account = str(selected.get("account_number") or "").strip()
    selected_bank_code = str(selected.get("bank_code") or "").strip()
    selected_bank_code_provider = str(selected.get("bank_code_provider") or "mono").strip().lower()
    selected_bank_name = str(selected.get("bank_name") or "").strip()

    payload.beneficiary_id = None
    payload.recipient_resolved_name = None
    payload.resolved_from_saved_beneficiary = False

    req_account = str(payload.recipient_account or "").strip()
    if req_account and selected_account and req_account == selected_account:
        payload.recipient_account = None

    req_bank_code = str(payload.recipient_bank_code or "").strip()
    req_bank_code_provider = str(payload.recipient_bank_code_provider or selected_bank_code_provider).strip().lower()
    if (
        req_bank_code
        and selected_bank_code
        and req_bank_code_provider == selected_bank_code_provider
        and req_bank_code == selected_bank_code
    ):
        payload.recipient_bank_code = None
        payload.recipient_bank_code_provider = None
        payload.recipient_resolution_provider = None

    req_bank_name = str(payload.recipient_bank_name or "").strip()
    if req_bank_name and selected_bank_name and _normalize_name(req_bank_name) == _normalize_name(selected_bank_name):
        payload.recipient_bank_name = None


def _compute_missing_recipient_fields(payload: TransferPayload) -> list[str]:
    required_fields: list[str] = []
    if not payload.recipient_account:
        required_fields.append("recipient_account")
    if not payload.recipient_bank_name and not payload.recipient_bank_code:
        required_fields.append("recipient_bank_name")
    return required_fields


def _is_transfer_beneficiary_record(record: dict[str, Any]) -> bool:
    """Allow transfer beneficiaries and legacy records without a type field."""
    beneficiary_type = record.get("beneficiary_type")
    if beneficiary_type is None:
        return True
    return str(beneficiary_type).strip().lower() == "transfer"


def _resolve_beneficiary_from_reference(
    payload: TransferPayload,
    candidates: list[Beneficiary],
) -> Beneficiary | None:
    reference = payload.recipient_reference if isinstance(payload.recipient_reference, dict) else None
    if not reference:
        return None

    selector = str(reference.get("selector") or "").strip().lower()
    if selector == "index":
        try:
            index = int(reference.get("index"))
        except (TypeError, ValueError):
            return None
        if 1 <= index <= len(candidates):
            return candidates[index - 1]
        return None

    return None


def _build_single_beneficiary_patch(single: Beneficiary, recipient_name: str | None) -> dict[str, Any]:
    requested_alias = str(recipient_name or "").strip()
    beneficiary_alias = str(single.alias or "").strip()
    account_name = str(single.account_name or "").strip()
    resolved_name = account_name or beneficiary_alias or requested_alias or None
    alias_name = requested_alias or beneficiary_alias or account_name or None
    bank_code = _optional_text(single.bank_code)
    provider = _beneficiary_provider(None, bank_code)

    return {
        "recipient_account": _optional_text(single.account_number),
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


def _build_single_beneficiary_patch_from_record(record: dict[str, Any], recipient_name: str | None) -> dict[str, Any]:
    requested_alias = str(recipient_name or "").strip()
    beneficiary_alias = str(record.get("alias") or "").strip()
    account_name = str(record.get("account_name") or "").strip()
    resolved_name = account_name or beneficiary_alias or requested_alias or None
    alias_name = requested_alias or beneficiary_alias or account_name or None
    beneficiary_id = str(record.get("id") or "").strip() or None
    bank_code = _optional_text(record.get("bank_code"))
    provider = _beneficiary_provider(record.get("bank_code_provider"), bank_code)
    resolution_provider = _beneficiary_provider(record.get("resolution_provider"), bank_code)

    return {
        "recipient_account": _optional_text(record.get("account_number")),
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


async def _saved_beneficiary_result(
    patch: dict[str, Any],
    payload: TransferPayload,
    locale: str,
    resolver_provider: Any | None,
    bank_cache: Any | None,
) -> TransactionResult:
    """Return a saved-beneficiary patch, resolving Mono bank code when the saved record has only a bank name."""
    if patch.get("recipient_bank_code"):
        return TransactionResult(outcome=TransactionOutcome.OK, patch=patch)

    account_number = _optional_text(patch.get("recipient_account"))
    bank_name = _optional_text(patch.get("recipient_bank_name"))
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

    resolver_name = _provider_name(resolver_provider)
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
                f"{_ask_account_and_bank_prompt(locale, patch.get('recipient_name'))}"
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
    patch.update(_build_name_consistency_patch(consistency_payload, resolved_name, locale))
    return TransactionResult(outcome=TransactionOutcome.OK, patch=patch)


def _build_beneficiary_clarify_result(
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


def _ask_account_and_bank_prompt(locale: str, recipient_name: str | None) -> str:
    fallback_name = sanitize_recipient_display_name(recipient_name, locale)
    return render_message(
        "response.templates.ask_account_number_and_bank",
        locale,
        {"recipient_name": fallback_name},
    )


class ResolutionStep(TransferStep):
    """Resolves beneficiary details."""

    async def execute(
        self,
        data: TransferPayload,
        context: TransferContext,
        gates: TransferGates,
        worker_context: Any,
    ) -> TransactionResult:
        del gates
        return await resolve_beneficiary(
            data,
            context,
            worker_context.resolver_provider,
            worker_context.bank_cache,
        )


async def resolve_beneficiary(
    payload: TransferPayload,
    ctx: TransferContext,
    resolver_provider: Any | None = None,
    bank_cache: Any | None = None,
) -> TransactionResult:
    """Resolve recipient name to bank details using BeneficiaryMatcher."""
    locale = ctx.language
    if payload.beneficiary_id:
        selected_id = _canonical_beneficiary_id(payload.beneficiary_id)
        selected = next((b for b in ctx.beneficiaries if str(b.get("id")) == selected_id), None)
        if selected:
            if not _matches_selected_beneficiary(payload, selected):
                logger.info(
                    "beneficiary_binding_overridden",
                    beneficiary_id=selected_id,
                    recipient_name=payload.recipient_name,
                )
                _clear_stale_beneficiary_binding(payload, selected)
            else:
                current_name = str(payload.recipient_name or "").strip()
                selected_alias = str(selected.get("alias") or "").strip()
                selected_account_name = str(selected.get("account_name") or "").strip()
                recipient_name = current_name or selected_alias or selected_account_name or None
                resolved_name = selected_account_name or selected_alias or current_name or None
                bank_code = _optional_text(selected.get("bank_code"))
                provider = _beneficiary_provider(selected.get("bank_code_provider"), bank_code)
                resolution_provider = _beneficiary_provider(selected.get("resolution_provider"), bank_code)
                return await _saved_beneficiary_result(
                    {
                        "recipient_account": _optional_text(selected.get("account_number")),
                        "recipient_bank_code": bank_code,
                        "recipient_bank_name": selected.get("bank_name"),
                        "recipient_bank_code_provider": provider,
                        "recipient_resolution_provider": resolution_provider or provider,
                        "recipient_name": recipient_name,
                        "recipient_resolved_name": resolved_name,
                        "resolved_from_saved_beneficiary": True,
                        "name_mismatch": False,
                        "name_match_score": None,
                        "name_mismatch_warning": None,
                        "beneficiary_candidates": [],
                    },
                    payload,
                    locale,
                    resolver_provider,
                    bank_cache,
                )

    if payload.recipient_resolved_name:
        return TransactionResult(outcome=TransactionOutcome.OK)

    raw_recipient_name = payload.recipient_name
    recipient_name_for_match = None if _is_unsafe_recipient_placeholder(raw_recipient_name) else raw_recipient_name

    # 1b. Resolve Bank Code if missing
    if payload.recipient_bank_name and not payload.recipient_bank_code:
        if bank_cache:
            # Ensure banks are loaded in cache
            if resolver_provider:
                await bank_cache.ensure_banks_cached(resolver_provider.get_banks)

            code = await bank_cache.get_bank_code(payload.recipient_bank_name)
            if code:
                # Update payload directly as we are about to use it for account resolution
                payload.recipient_bank_code = code
                payload.recipient_bank_code_provider = _provider_name(bank_cache)

    if payload.recipient_account and payload.recipient_bank_code and not payload.recipient_resolved_name:
        if resolver_provider:
            try:
                resolver_name = _provider_name(resolver_provider)
                logger.info(
                    "resolving_recipient_account",
                    account=payload.recipient_account,
                    bank_code=payload.recipient_bank_code,
                    provider=resolver_name,
                )
                resolved = await resolver_provider.resolve_account(
                    payload.recipient_account, payload.recipient_bank_code
                )
                if resolved and resolved.success and resolved.account:
                    resolved_name = resolved.account.account_name
                    patch = {
                        "recipient_resolved_name": resolved_name,
                        "recipient_bank_name": payload.recipient_bank_name,
                        "recipient_bank_code": resolved.account.bank_code or payload.recipient_bank_code,
                        "recipient_bank_code_provider": resolver_name,
                        "recipient_resolution_provider": resolver_name,
                        "resolved_from_saved_beneficiary": False,
                    }
                    if (not payload.recipient_name or _is_unsafe_recipient_placeholder(payload.recipient_name)) and resolved_name:
                        patch["recipient_name"] = resolved_name
                    patch.update(_build_name_consistency_patch(payload, resolved_name, locale))
                    return TransactionResult(
                        outcome=TransactionOutcome.OK,
                        patch=patch,
                    )
            except Exception as e:
                logger.warning(
                    "recipient_account_resolution_failed",
                    error=str(e),
                    account=payload.recipient_account,
                    bank_code=payload.recipient_bank_code,
                    provider=_provider_name(resolver_provider),
                )

        if not payload.recipient_name and not payload.recipient_resolved_name:
            # If account verification fails below, request account+bank re-entry.
            pass

    transfer_beneficiaries_raw = [b for b in ctx.beneficiaries if _is_transfer_beneficiary_record(b)]
    beneficiaries = [Beneficiary(**b) for b in transfer_beneficiaries_raw]

    beneficiary_from_reference = _resolve_beneficiary_from_reference(
        payload,
        beneficiaries,
    )
    if beneficiary_from_reference:
        return await _saved_beneficiary_result(
            _build_single_beneficiary_patch(beneficiary_from_reference, recipient_name_for_match),
            payload,
            locale,
            resolver_provider,
            bank_cache,
        )

    previous_beneficiary = ctx.previous_beneficiary if isinstance(ctx.previous_beneficiary, dict) else None
    previous_reference = payload.recipient_reference if isinstance(payload.recipient_reference, dict) else None
    if previous_beneficiary:
        if previous_reference and str(previous_reference.get("selector") or "").strip().lower() == "previous":
            return await _saved_beneficiary_result(
                _build_single_beneficiary_patch_from_record(previous_beneficiary, recipient_name_for_match),
                payload,
                locale,
                resolver_provider,
                bank_cache,
            )
        if _is_pronoun_recipient(raw_recipient_name):
            return await _saved_beneficiary_result(
                _build_single_beneficiary_patch_from_record(previous_beneficiary, recipient_name_for_match),
                payload,
                locale,
                resolver_provider,
                bank_cache,
            )

    if _is_pronoun_recipient(raw_recipient_name):
        if not ctx.recent_beneficiary_context:
            recipient_name_for_match = None
        elif len(beneficiaries) == 1:
            return await _saved_beneficiary_result(
                _build_single_beneficiary_patch(beneficiaries[0], recipient_name_for_match),
                payload,
                locale,
                resolver_provider,
                bank_cache,
            )
        elif len(beneficiaries) > 1:
            return _build_beneficiary_clarify_result(recipient_name_for_match, beneficiaries, locale)
        else:
            recipient_name_for_match = None

    if not recipient_name_for_match:
        # 2b. If we have an account number, we tried resolution above and failed (or bank was missing)
        if payload.recipient_account:
            if not payload.recipient_bank_code and not payload.recipient_bank_name:
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["recipient_bank_name"],
                    prompt=render_message(
                        "transfer.resolve.need_bank_name_for_account",
                        locale,
                        {"recipient_account": payload.recipient_account},
                    ),
                )
            # If we have Bank Name but no Code -> Bank Lookup Failed
            if payload.recipient_bank_name and not payload.recipient_bank_code:
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["recipient_bank_name"],
                    prompt=render_message(
                        "transfer.resolve.bank_name_not_found",
                        locale,
                        {"bank_name": payload.recipient_bank_name},
                    ),
                )

            # If we have bank code + account but still cannot resolve, request a full re-entry.
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["recipient_account", "recipient_bank_name"],
                prompt=(
                    f"{render_message('response.templates.account_validation_failed', locale)} "
                    f"{_ask_account_and_bank_prompt(locale, raw_recipient_name)}"
                ),
            )

        if payload.recipient_bank_name or payload.recipient_bank_code:
            fallback_name = sanitize_recipient_display_name(raw_recipient_name, locale)
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["recipient_account"],
                prompt=render_message(
                    "response.templates.ask_account_number",
                    locale,
                    {"recipient_name": fallback_name},
                ),
            )

        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=["recipient_account", "recipient_bank_name"],
            prompt=_ask_account_and_bank_prompt(locale, raw_recipient_name),
        )

    bank_term = (payload.recipient_bank_name or "").lower()

    own_accounts = ctx.accounts or []
    candidate_account = None

    if payload.is_self:
        if bank_term:
            candidate_account = next(
                (a for a in own_accounts if bank_term in (a.get("bank_name") or "").lower()),
                None,
            )
        elif payload.is_self and len(own_accounts) == 2:
            # "Send to myself" (no bank specified) - Smart Inference
            # If we know the source, the recipient MUST be the other account
            source_id = payload.source_account_id

            # If source ID missing, try resolving from bank name
            if not source_id and payload.source_bank_name:
                src_bank = payload.source_bank_name.lower()
                src_match = next(
                    (a for a in own_accounts if src_bank in (a.get("bank_name") or "").lower()),
                    None,
                )
                if src_match:
                    source_id = str(src_match.get("id"))

            if source_id:
                candidate_account = next(
                    (a for a in own_accounts if str(a.get("id")) != source_id),
                    None,
                )

    if candidate_account:
        # Confirm intent: User likely meant this account if they specified the bank
        # and didn't provide an external account number
        if not payload.recipient_account:
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch={
                    "recipient_account": str(candidate_account.get("account_number")),
                    "recipient_bank_code": str(candidate_account.get("bank_code")),
                    "recipient_bank_name": candidate_account.get("bank_name"),
                    "recipient_bank_code_provider": "mono",
                    "recipient_resolution_provider": "mono",
                    "recipient_resolved_name": render_message(
                        "transfer.resolve.my_bank_account",
                        locale,
                        {"bank_name": candidate_account.get("bank_name")},
                    ),
                    "recipient_name": render_message(
                        "transfer.resolve.my_bank_name",
                        locale,
                        {"bank_name": candidate_account.get("bank_name")},
                    ),
                    "is_self": True,
                    "resolved_from_saved_beneficiary": False,
                    "name_mismatch": False,
                    "name_match_score": None,
                    "name_mismatch_warning": None,
                },
            )

    matcher = BeneficiaryMatcher()
    logger.info(
        "beneficiary_match_attempt",
        recipient_name=recipient_name_for_match,
        beneficiary_count=len(beneficiaries),
    )

    status, single, candidates = matcher.match(recipient_name_for_match, beneficiaries)
    logger.info(
        "beneficiary_match_result",
        status=status,
        candidate_count=len(candidates),
        matched=bool(single),
    )

    if status == "single" and single:
        return await _saved_beneficiary_result(
            _build_single_beneficiary_patch(single, recipient_name_for_match),
            payload,
            locale,
            resolver_provider,
            bank_cache,
        )
    elif status == "clarify" and candidates:
        return _build_beneficiary_clarify_result(recipient_name_for_match, candidates, locale)

    required_fields = _compute_missing_recipient_fields(payload)
    missing = []
    if "recipient_account" in required_fields:
        missing.append(render_message("transfer.resolve.missing_account_number", locale))
    if "recipient_bank_name" in required_fields:
        missing.append(render_message("transfer.resolve.missing_bank_name", locale))

    if missing:
        missing_str = " and ".join(missing)

        # [UX] Conversational Prompt
        # Acknowledge what we know (Recipient + Amount) before asking for what's missing.
        recipient_display_name = sanitize_recipient_display_name(recipient_name_for_match, locale)
        base = render_message("transfer.resolve.ready_to_send", locale, {"recipient_name": recipient_display_name})
        if payload.amount:
            amt = payload.amount
            if isinstance(amt, (int, float)):
                amt = format_naira_compact(amt)
            base = render_message(
                "transfer.resolve.can_send_amount",
                locale,
                {"amount": str(amt), "recipient_name": recipient_display_name},
            )

        prompt = render_message(
            "transfer.resolve.need_missing_details",
            locale,
            {"base": base, "missing": missing_str},
        )

        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=required_fields,
            prompt=prompt,
        )

    return TransactionResult(outcome=TransactionOutcome.OK)
