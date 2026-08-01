"""Beneficiary resolution logic."""

from typing import Any

from banking.beneficiaries.services.matcher import BeneficiaryMatcher
from banking.presentation.formatters.currency import format_naira_compact
from banking.presentation.formatters.recipient_prompt_names import sanitize_recipient_display_name
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import TransactionOutcome, TransactionResult
from banking.transfers.models.types import (
    TransferContext,
    TransferPayload,
)
from banking.transfers.resolution.names import (
    ask_account_and_bank_prompt,
    beneficiary_provider,
    build_name_consistency_patch,
    canonical_beneficiary_id,
    clear_stale_beneficiary_binding,
    compute_missing_recipient_fields,
    is_pronoun_recipient,
    is_unsafe_recipient_placeholder,
    matches_selected_beneficiary,
    optional_text,
    provider_name,
)
from banking.transfers.resolution.saved_beneficiaries import (
    build_beneficiary_clarify_result,
    build_single_beneficiary_patch,
    is_transfer_beneficiary_record,
    resolve_beneficiary_from_reference,
    resolve_memory_recipient_result,
    saved_beneficiary_result,
)
from shared.config.settings import settings
from shared.database.models import Beneficiary
from shared.utils.logging import get_logger, log_orchestrator_diagnostic

logger = get_logger(__name__)


def _bank_alias_suffixes(bank_name: str | None) -> list[str]:
    raw = str(bank_name or "").strip()
    if not raw:
        return []

    values = [raw]
    lowered = raw.lower()
    if lowered.endswith(" bank"):
        values.append(raw[: -len(" bank")].strip())

    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return unique


def _recipient_bank_alias_candidates(payload: TransferPayload, recipient_name: str | None) -> list[str]:
    name = str(recipient_name or "").strip()
    if not name:
        return []

    candidates: list[str] = []
    seen: set[str] = set()
    for bank_suffix in _bank_alias_suffixes(payload.recipient_bank_name):
        candidate = f"{name} {bank_suffix}".strip()
        key = candidate.lower()
        if candidate and key not in seen:
            seen.add(key)
            candidates.append(candidate)
    return candidates


async def resolve_beneficiary(
    payload: TransferPayload,
    ctx: TransferContext,
    resolver_provider: Any | None = None,
    bank_cache: Any | None = None,
) -> TransactionResult:
    """Resolve recipient name to bank details using BeneficiaryMatcher."""
    locale = ctx.language
    if payload.beneficiary_id:
        selected_id = canonical_beneficiary_id(payload.beneficiary_id)
        selected = next((b for b in ctx.beneficiaries if str(b.get("id")) == selected_id), None)
        if selected:
            selection_ref = payload.beneficiary_selection_ref
            if selection_ref is not None:
                updated_at = selected.get("updated_at")
                isoformat = getattr(updated_at, "isoformat", None)
                version_token = (
                    str(isoformat() if callable(isoformat) else updated_at) if updated_at is not None else None
                )
                if version_token != selection_ref.version_token:
                    return TransactionResult(
                        outcome=TransactionOutcome.NEEDS_INPUT,
                        required_fields=["beneficiary_id"],
                        prompt=render_message("conversation_set.stale_selection", locale),
                    )
            if not matches_selected_beneficiary(payload, selected):
                logger.info(
                    "beneficiary_binding_overridden",
                    beneficiary_id=selected_id,
                    recipient_name=payload.recipient_name,
                )
                clear_stale_beneficiary_binding(payload, selected)
            else:
                current_name = str(payload.recipient_name or "").strip()
                selected_alias = str(selected.get("alias") or "").strip()
                selected_account_name = str(
                    selected.get("account_name") or selected.get("recipient_resolved_name") or ""
                ).strip()
                recipient_name = current_name or selected_alias or selected_account_name or None
                resolved_name = selected_account_name or selected_alias or current_name or None
                account_number = optional_text(
                    selected.get("account_number") or selected.get("recipient_account") or payload.recipient_account
                )
                bank_code = optional_text(
                    selected.get("bank_code") or selected.get("recipient_bank_code") or payload.recipient_bank_code
                )
                bank_name = (
                    selected.get("bank_name") or selected.get("recipient_bank_name") or payload.recipient_bank_name
                )
                provider = beneficiary_provider(
                    selected.get("bank_code_provider")
                    or selected.get("recipient_bank_code_provider")
                    or payload.recipient_bank_code_provider,
                    bank_code,
                )
                resolution_provider = beneficiary_provider(
                    selected.get("resolution_provider")
                    or selected.get("recipient_resolution_provider")
                    or payload.recipient_resolution_provider,
                    bank_code,
                )
                return await saved_beneficiary_result(
                    {
                        "recipient_account": account_number,
                        "recipient_bank_code": bank_code,
                        "recipient_bank_name": bank_name,
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

    if (
        payload.recipient_resolved_name
        and payload.recipient_account
        and (payload.recipient_bank_code or payload.recipient_bank_name)
    ):
        return TransactionResult(outcome=TransactionOutcome.OK)

    raw_recipient_name = payload.recipient_name
    recipient_name_for_match = None if is_unsafe_recipient_placeholder(raw_recipient_name) else raw_recipient_name

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
                payload.recipient_bank_code_provider = provider_name(bank_cache)

    if payload.recipient_account and payload.recipient_bank_code and not payload.recipient_resolved_name:
        if resolver_provider:
            try:
                resolver_name = provider_name(resolver_provider)
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
                    if (
                        not payload.recipient_name or is_unsafe_recipient_placeholder(payload.recipient_name)
                    ) and resolved_name:
                        patch["recipient_name"] = resolved_name
                    patch.update(build_name_consistency_patch(payload, resolved_name, locale))
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
                    provider=provider_name(resolver_provider),
                )

        if not payload.recipient_name and not payload.recipient_resolved_name:
            # If account verification fails below, request account+bank re-entry.
            pass

    transfer_beneficiaries_raw = [b for b in ctx.beneficiaries if is_transfer_beneficiary_record(b)]
    beneficiaries = [Beneficiary(**b) for b in transfer_beneficiaries_raw]

    beneficiary_from_reference = resolve_beneficiary_from_reference(
        payload,
        beneficiaries,
    )
    if beneficiary_from_reference:
        return await saved_beneficiary_result(
            build_single_beneficiary_patch(beneficiary_from_reference, recipient_name_for_match),
            payload,
            locale,
            resolver_provider,
            bank_cache,
        )

    previous_reference = payload.recipient_reference if isinstance(payload.recipient_reference, dict) else None

    if is_pronoun_recipient(raw_recipient_name) or (
        previous_reference and str(previous_reference.get("selector") or "").strip().lower() == "previous"
    ):
        memory_result = await resolve_memory_recipient_result(payload, ctx, locale, resolver_provider, bank_cache)
        if memory_result is not None:
            return memory_result
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
                    f"{ask_account_and_bank_prompt(locale, raw_recipient_name)}"
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
            prompt=ask_account_and_bank_prompt(locale, raw_recipient_name),
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
                    "recipient_bank_code_provider": settings.transfer_resolver_provider_name,
                    "recipient_resolution_provider": settings.transfer_resolver_provider_name,
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
    log_orchestrator_diagnostic(
        logger,
        "beneficiary_match_attempt",
        recipient_name=recipient_name_for_match,
        beneficiary_count=len(beneficiaries),
    )

    for alias_candidate in _recipient_bank_alias_candidates(payload, recipient_name_for_match):
        exact_candidates = matcher.exact_matches(alias_candidate, beneficiaries)
        if not exact_candidates:
            continue
        log_orchestrator_diagnostic(
            logger,
            "beneficiary_exact_alias_match_attempt",
            alias_candidate=alias_candidate,
            candidate_count=len(exact_candidates),
        )
        if len(exact_candidates) == 1:
            return await saved_beneficiary_result(
                build_single_beneficiary_patch(exact_candidates[0], alias_candidate),
                payload,
                locale,
                resolver_provider,
                bank_cache,
            )
        return build_beneficiary_clarify_result(alias_candidate, exact_candidates, locale)

    status, single, candidates = matcher.match(recipient_name_for_match, beneficiaries)
    log_orchestrator_diagnostic(
        logger,
        "beneficiary_match_result",
        status=status,
        candidate_count=len(candidates),
        matched=bool(single),
    )

    if status == "single" and single:
        return await saved_beneficiary_result(
            build_single_beneficiary_patch(single, recipient_name_for_match),
            payload,
            locale,
            resolver_provider,
            bank_cache,
        )
    elif status == "clarify" and candidates:
        return build_beneficiary_clarify_result(recipient_name_for_match, candidates, locale)

    required_fields = compute_missing_recipient_fields(payload)
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
            amt = (
                format_naira_compact(payload.amount)
                if isinstance(payload.amount, (int, float))
                else str(payload.amount)
            )
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
