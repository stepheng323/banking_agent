"""Airtime extraction step."""

import re
from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from apps.chat.src.agent.workers.__shared__.account_selection.reference import (
    build_source_account_patch,
    match_source_account_reference,
)
from apps.chat.src.agent.workers.__shared__.extraction_utils import try_extract_numeric_index
from apps.chat.src.agent.workers.__shared__.scheduling import (
    SCHEDULE_FIELD_NAMES,
    parse_schedule_slot_patch,
    schedule_required_prompt,
)
from apps.chat.src.agent.workers.__shared__.source_account_guard import find_account_by_bank_name
from apps.chat.src.agent.workers.airtime.models.types import (
    AirtimeContext,
    AirtimeGates,
    AirtimePayload,
)
from apps.chat.src.agent.workers.airtime.pipeline.base import AirtimeStep
from shared.i18n.renderer import render_message
from shared.utils.bank_aliases import get_bank_search_terms
from shared.utils.logging import get_logger
from shared.utils.network_utils import normalize_network_name, normalize_nigerian_phone

logger = get_logger(__name__)
_NETWORK_CANONICAL = {"MTN", "AIRTEL", "GLO", "9MOBILE"}
_PHONE_CANDIDATE_PATTERN = re.compile(r"(?:\+?234|0)?(?:[\s().-]*\d){10,13}")
_NETWORK_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")
_AMOUNT_REPLY_PATTERN = re.compile(
    r"^\s*(?:₦|ngn)?\s*(?P<amount>\d[\d,]*(?:\.\d+)?)\s*(?P<suffix>[kKhH]?)"
    r"\s*(?:naira|ngn)?\s*[.!?]?\s*$",
    re.IGNORECASE,
)
_SOURCE_BANK_PREFIX_RE = r"(?:from|using|use|with|debit(?:ing)?|charge)"
_SOURCE_ACCOUNT_PATCH_FIELDS = {
    "source_account_id",
    "source_bank_name",
    "source_account_name",
    "source_account_number",
    "source_account_index",
}


def _matches_self_airtime_phrase(message: str) -> bool:
    """Detect narrow deterministic self-airtime phrases."""
    lowered = message.lower()

    if any(token in lowered for token in ("my line", "my number", "myself", "for me")):
        return True

    return re.search(r"\bbuy me\b[\w\s]{0,80}\bairtime\b", lowered) is not None


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


def _parse_amount_reply(message: str) -> float | None:
    match = _AMOUNT_REPLY_PATTERN.fullmatch(message.strip())
    if not match:
        return None
    try:
        amount = float(match.group("amount").replace(",", ""))
    except ValueError:
        return None
    suffix = match.group("suffix").lower()
    if suffix == "k":
        amount *= 1000
    elif suffix == "h":
        amount *= 100
    return amount if amount > 0 else None


def _source_bank_terms(bank_name: str) -> list[str]:
    terms = {term.strip().lower() for term in get_bank_search_terms(bank_name) if term.strip()}
    normalized = bank_name.strip().lower()
    if normalized:
        terms.add(normalized)
        for suffix in (" bank", " plc", " limited"):
            if normalized.endswith(suffix):
                terms.add(normalized[: -len(suffix)].strip())
    return sorted(terms, key=len, reverse=True)


def _extract_source_bank_hint(message: str, accounts: list[dict[str, Any]]) -> str | None:
    normalized_message = re.sub(r"\s+", " ", message.strip().lower())
    if not normalized_message:
        return None

    for account in accounts:
        bank_name = str(account.get("bank_name") or "").strip()
        if not bank_name:
            continue
        for term in _source_bank_terms(bank_name):
            escaped = re.escape(term).replace(r"\ ", r"\s+")
            if re.search(rf"\b{_SOURCE_BANK_PREFIX_RE}\s+(?:my\s+)?{escaped}(?:\s+(?:account|acct|bank))?\b", normalized_message):
                return bank_name
    return None


def _build_mobile_source_account_patch(account: dict[str, Any]) -> dict[str, Any]:
    patch = build_source_account_patch(account)
    return {field: patch.get(field) for field in _SOURCE_ACCOUNT_PATCH_FIELDS if field in patch}


def _skip_override_reason(payload: AirtimePayload, message: str) -> str | None:
    normalized_phone = normalize_nigerian_phone(str(payload.recipient_phone or ""))
    if normalized_phone is None and _matches_self_airtime_phrase(message):
        return "missing_recipient_phone_with_self_signal"
    if normalized_phone is None and _has_phone_signal(message):
        return "missing_recipient_phone_with_phone_signal"
    if not _has_resolved_network(payload.network) and _has_network_signal(message):
        return "missing_network_with_network_signal"
    return None


def _resolved_phone_referent(context: AirtimeContext) -> dict[str, Any] | None:
    return _resolved_referent_data(context, "phone")


def _resolved_referent_data(context: AirtimeContext, referent_type: str) -> dict[str, Any] | None:
    resolution = context.resolved_referents.get(referent_type)
    if not isinstance(resolution, dict) or resolution.get("status") != "resolved":
        return None
    item = resolution.get("item")
    if not isinstance(item, dict):
        return None
    data = item.get("data")
    return data if isinstance(data, dict) else None


def _add_resolved_referent_patch(
    patch: dict[str, Any],
    payload: AirtimePayload,
    context: AirtimeContext,
) -> None:
    if not patch.get("recipient_phone") and not payload.recipient_phone:
        phone_referent = _resolved_phone_referent(context)
        phone = normalize_nigerian_phone(str((phone_referent or {}).get("phone") or ""))
        if phone:
            patch["recipient_phone"] = phone
            if not patch.get("network") and not payload.network and phone_referent and phone_referent.get("network"):
                normalized_network = normalize_network_name(str(phone_referent["network"]))
                patch["network"] = normalized_network or str(phone_referent["network"]).strip().upper()

    if "amount" not in patch and payload.amount is None:
        amount_referent = _resolved_referent_data(context, "amount")
        if amount_referent:
            try:
                amount = float(amount_referent.get("amount"))
            except (TypeError, ValueError):
                amount = None
            if amount is not None and amount > 0:
                patch["amount"] = amount

    source_has_value = any(
        (
            patch.get("source_account_id"),
            patch.get("source_bank_name"),
            patch.get("source_account_number"),
            payload.source_account_id,
            payload.source_bank_name,
            payload.source_account_number,
            payload.source_account_index is not None,
        )
    )
    if source_has_value:
        return
    source_referent = _resolved_referent_data(context, "source_account")
    if not source_referent:
        return
    account_id = source_referent.get("source_account_id") or source_referent.get("account_id")
    bank_name = source_referent.get("source_bank_name") or source_referent.get("bank_name")
    account_name = source_referent.get("source_account_name") or source_referent.get("account_name")
    account_number = source_referent.get("source_account_number") or source_referent.get("account_number")
    if not (account_id or bank_name or account_number):
        return
    patch["source_account_id"] = str(account_id).strip() if account_id else None
    patch["source_bank_name"] = bank_name
    patch["source_account_name"] = account_name
    patch["source_account_number"] = account_number
    patch["source_account_index"] = None


def _referent_phone_candidates(context: AirtimeContext) -> list[dict[str, Any]]:
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
        phone = normalize_nigerian_phone(str(data.get("phone") or data.get("recipient_phone") or item.get("label") or ""))
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
                    "recipient_phone": phone,
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
        "recipient_phone": selected.get("recipient_phone"),
        "referent_phone_candidates": [],
    }
    if selected.get("network"):
        patch["network"] = selected["network"]
    if selected.get("recipient_name"):
        patch["recipient_name"] = selected["recipient_name"]
    return patch, None


class ExtractionStep(AirtimeStep):
    """Refines payload with extraction from current message."""

    def __init__(self, user_message: str | None):
        self.user_message = user_message

    async def execute(
        self,
        data: AirtimePayload,
        context: AirtimeContext,
        gates: AirtimeGates,
        worker_context: Any,
    ) -> TransactionResult:
        del gates
        if not self.user_message:
            return TransactionResult(outcome=TransactionOutcome.OK)

        skip_requested = data.skip_extraction

        def _with_skip_patch(patch: dict[str, Any] | None = None) -> dict[str, Any] | None:
            if not skip_requested:
                return patch
            merged = dict(patch or {})
            merged.setdefault("skip_extraction", False)
            return merged

        # [DETERMINISTIC FALLBACK] Numeric index selection
        # If user replies with "1" or "2" while selecting source account, map it directly.
        raw_required_fields = getattr(worker_context, "required_fields", [])
        required_fields = raw_required_fields if isinstance(raw_required_fields, list) else []
        waiting_for_source_account = "source_account_id" in required_fields
        waiting_for_recipient_phone = "recipient_phone" in required_fields or "phone_number" in required_fields
        waiting_for_amount = "amount" in required_fields
        waiting_for_referent_phone = "referent_phone_id" in required_fields
        waiting_for_network = "network" in required_fields
        schedule_required_fields = [field for field in required_fields if field in SCHEDULE_FIELD_NAMES]
        if waiting_for_amount and data.amount is None:
            amount = _parse_amount_reply(self.user_message)
            if amount is not None:
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    patch=_with_skip_patch({"amount": amount}),
                )
        if waiting_for_referent_phone:
            referent_patch, invalid_referents = _resolve_referent_phone_selection_from_input(
                self.user_message,
                data.referent_phone_candidates,
            )
            if referent_patch:
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    patch=_with_skip_patch(referent_patch),
                )
            if invalid_referents:
                retry_prompt = _ambiguous_phone_referent_prompt(invalid_referents, context.language)
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["referent_phone_id"],
                    prompt=retry_prompt,
                    patch=_with_skip_patch({"referent_phone_candidates": invalid_referents}),
                    details={
                        "ambiguity": "MULTIPLE_REFERENT_PHONES",
                        "candidates": invalid_referents,
                    },
                )
        if not data.recipient_phone:
            phone_candidates = _referent_phone_candidates(context)
            ambiguity_prompt = _ambiguous_phone_referent_prompt(phone_candidates, context.language)
            if ambiguity_prompt:
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["referent_phone_id"],
                    prompt=ambiguity_prompt,
                    patch=_with_skip_patch({"referent_phone_candidates": phone_candidates}),
                    details={
                        "ambiguity": "MULTIPLE_REFERENT_PHONES",
                        "candidates": phone_candidates,
                    },
                )
        if schedule_required_fields:
            schedule_patch, remaining_schedule_fields = parse_schedule_slot_patch(
                self.user_message,
                schedule_required_fields,
            )
            if schedule_patch:
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    patch=_with_skip_patch(schedule_patch),
                )
            if len(schedule_required_fields) == len(required_fields):
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=remaining_schedule_fields or schedule_required_fields,
                    prompt=schedule_required_prompt(remaining_schedule_fields or schedule_required_fields, context.language),
                    patch=_with_skip_patch({"is_scheduled_operation": True, "skip_finalize_summary": True}),
                )
        source_account = None
        if waiting_for_source_account:
            source_account = match_source_account_reference(
                self.user_message,
                [account for account in (context.all_accounts or context.accounts) if isinstance(account, dict)],
            )
        numeric_patch = None
        if waiting_for_source_account and not (
            self.user_message.strip().isdigit() and len(self.user_message.strip()) >= 4
        ):
            numeric_patch = try_extract_numeric_index(self.user_message, "airtime")
        if numeric_patch:
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch=_with_skip_patch(numeric_patch),
            )
        if source_account:
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch=_with_skip_patch(_build_mobile_source_account_patch(source_account)),
            )

        referent_patch: dict[str, Any] = {}
        _add_resolved_referent_patch(referent_patch, data, context)
        if skip_requested and referent_patch:
            logger.info("deterministic_airtime_referent_fastpath", fields=sorted(referent_patch.keys()))
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch=_with_skip_patch(referent_patch),
            )

        if skip_requested:
            override_reason = _skip_override_reason(data, self.user_message)
            if override_reason is None:
                logger.info("skip_redundant_extraction", task="airtime", reason="no_override_signal")
                return TransactionResult(outcome=TransactionOutcome.OK, patch=_with_skip_patch({}))
            logger.info("override_skip_extraction", task="airtime", reason=override_reason)

        extractor = worker_context.extractor
        if not extractor:
            logger.warning("airtime_extractor_missing")
            patch: dict[str, Any] = {}
            _add_resolved_referent_patch(patch, data, context)
            if waiting_for_recipient_phone and not (patch.get("recipient_phone") or data.recipient_phone):
                fallback_phone = normalize_nigerian_phone(self.user_message)
                if fallback_phone:
                    patch["recipient_phone"] = fallback_phone
                elif _matches_self_airtime_phrase(self.user_message):
                    patch["is_self"] = True
            if waiting_for_network and not _has_resolved_network(str(patch.get("network") or data.network or "")):
                fallback_network = normalize_network_name(self.user_message)
                if fallback_network:
                    patch["network"] = fallback_network
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch=_with_skip_patch(patch),
            )

        temp_state = {
            "message": self.user_message,
            "amount": data.amount,
            "recipient_phone": data.recipient_phone,
            "network": data.network,
            "recipient_name": data.recipient_name,
            "beneficiaries": context.beneficiaries,
            "accounts": context.accounts,
            "required_fields": required_fields,
            "previousResponse": getattr(worker_context, "previous_response", None),
            "language": context.language,
        }

        try:
            extracted = await extractor.run(temp_state)

            entities = extracted.get("entities", {})

            patch = {}

            if entities.get("amount"):
                patch["amount"] = entities["amount"]
            if entities.get("recipient_phone"):
                patch["recipient_phone"] = entities["recipient_phone"]
            if entities.get("recipient_name"):
                patch["recipient_name"] = entities["recipient_name"]
            if entities.get("network"):
                normalized_network = normalize_network_name(str(entities["network"]))
                patch["network"] = normalized_network or str(entities["network"]).strip().upper()

            if entities.get("source_account_index") is not None:
                patch["source_account_index"] = entities["source_account_index"]
            if entities.get("source_bank_name"):
                patch["source_bank_name"] = str(entities["source_bank_name"]).strip()

            if not patch.get("recipient_phone") and not data.recipient_phone:
                phone_referent = _resolved_phone_referent(context)
                phone = normalize_nigerian_phone(str((phone_referent or {}).get("phone") or ""))
                if phone:
                    patch["recipient_phone"] = phone
                    if not patch.get("network") and phone_referent and phone_referent.get("network"):
                        normalized_network = normalize_network_name(str(phone_referent["network"]))
                        patch["network"] = normalized_network or str(phone_referent["network"]).strip().upper()

            correction = extracted.get("correction")
            if correction:
                field = correction.get("field")
                value = correction.get("new_value")
                if field == "amount" and value:
                    try:
                        patch["amount"] = float(value)
                    except (ValueError, TypeError):
                        pass
                elif field == "recipient_phone" and value:
                    patch["recipient_phone"] = str(value)
                elif field == "network" and value:
                    normalized_network = normalize_network_name(str(value))
                    patch["network"] = normalized_network or str(value).strip().upper()

            # [NARROW FALLBACK]
            # If this turn is explicitly waiting for a phone number and extractor misses it,
            # accept bare-number replies deterministically.
            existing_phone = patch.get("recipient_phone") or data.recipient_phone
            if waiting_for_recipient_phone and normalize_nigerian_phone(str(existing_phone or "")) is None:
                fallback_phone = normalize_nigerian_phone(self.user_message)
                if fallback_phone:
                    patch["recipient_phone"] = fallback_phone

            # [NARROW FALLBACK]
            # If this turn is explicitly waiting for the mobile network, accept only
            # exact network names/aliases so fresh requests still route elsewhere.
            existing_network = patch.get("network") or data.network
            if waiting_for_network and not _has_resolved_network(str(existing_network or "")):
                fallback_network = normalize_network_name(self.user_message)
                if fallback_network:
                    patch["network"] = fallback_network

            raw_is_self = entities.get("is_self")
            if raw_is_self is True:
                patch["is_self"] = True

            # [NARROW FALLBACK]
            # If extractor misses both recipient_phone and is_self on explicit
            # self-airtime phrasing, mark as self purchase.
            has_resolved_phone = bool(patch.get("recipient_phone") or data.recipient_phone)
            if raw_is_self is None and not has_resolved_phone and _matches_self_airtime_phrase(self.user_message):
                logger.info("airtime_self_fallback_applied")
                patch["is_self"] = True

            if not patch.get("source_bank_name") and not data.source_bank_name:
                bank_hint = _extract_source_bank_hint(self.user_message, context.all_accounts or context.accounts)
                if bank_hint:
                    matched_account = find_account_by_bank_name(context.all_accounts or context.accounts, bank_hint)
                    patch["source_bank_name"] = (
                        str(matched_account.get("bank_name") or bank_hint) if matched_account else bank_hint
                    )

            _add_resolved_referent_patch(patch, data, context)

            return TransactionResult(
                outcome=TransactionOutcome.OK,
                patch=_with_skip_patch(patch),
            )

        except Exception as e:
            logger.error("airtime_extraction_failed", error=str(e))
            return TransactionResult(outcome=TransactionOutcome.OK, patch=_with_skip_patch({}))
