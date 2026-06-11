"""Recent-batch support reference and receipt selection helpers."""

import re
import time
from typing import Literal

from banking.presentation.formatters.currency import format_naira
from banking.presentation.i18n.message_keys import MessageKey
from banking.presentation.i18n.renderer import render_message
from banking.support.models import (
    EPHEMERAL_CONTEXT_TTL_SECONDS,
    PendingReferenceState,
    ReceiptBatchSelection,
    ReceiptBatchSelectionRef,
    ReceiptBatchThreadState,
    SupportIntent,
    SupportReferenceCandidate,
)
from banking.transactions.runtime.async_group_types import RecentBatchLeg

_FinalStatus = Literal["success", "processing", "failed"]
_SelectionMode = Literal["all", "subset", "remainder"]

_ORDINAL_RE = re.compile(r"\b(?:(first|second|third|fourth|fifth|last)|([1-5])(?:st|nd|rd|th)?)\b", re.IGNORECASE)
_AMOUNT_RE = re.compile(r"(?:₦|ngn)?\s*(\d[\d,]*(?:\.\d+)?)\s*([kKhH]?)")
_AMOUNT_ONLY_SELECTOR_RE = re.compile(r"^(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKhH]?$", re.IGNORECASE)
_ORDINAL_ONLY_SELECTOR_RE = re.compile(
    r"^(?:the\s+)?(?:(?:first|second|third|fourth|fifth|last)(?:\s+one)?|[1-5](?:st|nd|rd|th)?)$",
    re.IGNORECASE,
)
_RECEIPT_ONLY_SELECTOR_RE = re.compile(
    r"^(?:send\s+)?(?:the\s+)?(?:receipt|receipts|proof|proof\s+of\s+payment)$",
    re.IGNORECASE,
)
_EXPLICIT_RECEIPT_SELECTION_RE = re.compile(
    r"\b(?:receipt|receipts|proof\s+of\s+payment|payment\s+proof)\b",
    re.IGNORECASE,
)
_REFERENCE_SELECTOR_ACTION_BLOCK_RE = re.compile(
    r"\b(?:send|transfer|pay|buy|recharge|top\s*up|topup|load|airtime|data|bundle|"
    r"balance|account|accounts|beneficiar(?:y|ies)|transaction|transactions|history|"
    r"how|what|when|where|why|show|list|check|get|fetch)\b",
    re.IGNORECASE,
)
_ALL_RECEIPTS_RE = re.compile(r"\b(?:all|every)\b.*\breceipts?\b|\breceipts?\b.*\b(?:all|every)\b", re.IGNORECASE)
_BOTH_RECEIPTS_RE = re.compile(r"\b(?:both|the two(?:\s+of\s+them)?|two of them)\b", re.IGNORECASE)
_OTHER_ONE_RE = re.compile(r"\b(?:the other one|other one|the other)\b", re.IGNORECASE)
_REMAINING_RE = re.compile(r"\b(?:the remaining ones?|remaining ones?|the rest|rest of them)\b", re.IGNORECASE)
_ALL_EXCEPT_RE = re.compile(
    r"\b(?:all|every|both)\b.*?\b(?:except|excluding|but not|apart from)\b(?P<tail>.+)$",
    re.IGNORECASE,
)
_ONLY_SELECTION_RE = re.compile(r"\b(?:only|just)\b(?P<tail>.+)$", re.IGNORECASE)


def normalize_match_text(value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.strip().lower()).strip()


def ordinal_from_message(message: str) -> int | None:
    match = _ORDINAL_RE.search(message)
    if match is None:
        return None
    word = str(match.group(1) or "").strip().lower()
    if word == "last":
        return -1
    if word in {"first", "second", "third", "fourth", "fifth"}:
        return {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}[word]
    digit = str(match.group(2) or "").strip()
    return int(digit) if digit.isdigit() else None


def ordinals_from_message(message: str) -> list[int]:
    ordinals: list[int] = []
    for match in _ORDINAL_RE.finditer(message):
        word = str(match.group(1) or "").strip().lower()
        if word == "last":
            value = -1
        elif word in {"first", "second", "third", "fourth", "fifth"}:
            value = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}[word]
        else:
            digit = str(match.group(2) or "").strip()
            if not digit.isdigit():
                continue
            value = int(digit)
        if value not in ordinals:
            ordinals.append(value)
    return ordinals


def amounts_from_message(message: str) -> list[float]:
    amounts: list[float] = []
    for match in _AMOUNT_RE.finditer(message):
        try:
            amount = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        suffix = (match.group(2) or "").lower()
        if suffix == "k":
            amount *= 1000.0
        elif suffix == "h":
            amount *= 100.0
        if amount > 0 and amount not in amounts:
            amounts.append(amount)
    return amounts


def _normalized_selector_text(message: str) -> str:
    return re.sub(r"\s+", " ", (message or "").strip()).strip(" \t\r\n.,;:!?\"'()[]{}")


def is_amount_only_selector_message(message: str) -> bool:
    """Return true when the whole message is just an amount selector."""
    return bool(_AMOUNT_ONLY_SELECTOR_RE.fullmatch(_normalized_selector_text(message)))


def is_strict_receipt_selector_message(message: str) -> bool:
    """Return true for terse receipt-thread selectors only."""
    normalized = _normalized_selector_text(message)
    if not normalized or len(normalized) > 80:
        return False
    if is_amount_only_selector_message(normalized):
        return True
    if _ORDINAL_ONLY_SELECTOR_RE.fullmatch(normalized):
        return True
    if _RECEIPT_ONLY_SELECTOR_RE.fullmatch(normalized):
        return True
    if re.fullmatch(r"(?:both|all|every)(?:\s+(?:of\s+)?(?:them|those|receipts?))?", normalized, re.IGNORECASE):
        return True
    if re.fullmatch(
        r"(?:the\s+)?(?:other(?:\s+one)?|remaining(?:\s+ones?)?|rest(?:\s+of\s+them)?)",
        normalized,
        re.IGNORECASE,
    ):
        return True
    only_match = re.fullmatch(r"(?:only|just)\s+(?P<tail>.+)", normalized, re.IGNORECASE)
    if only_match:
        tail = str(only_match.group("tail") or "")
        return is_strict_receipt_selector_message(tail)
    return False


def is_strict_reference_selector_message(message: str) -> bool:
    """Return true for terse pending-reference selectors.

    This is intentionally broader than receipt selection so a user can answer a
    pending support clarification with a short recipient label like "Tolu".
    """
    normalized = _normalized_selector_text(message)
    if not normalized or len(normalized) > 80:
        return False
    if is_strict_receipt_selector_message(normalized):
        return True
    if _REFERENCE_SELECTOR_ACTION_BLOCK_RE.search(normalized):
        return False
    tokens = re.findall(r"[\w']+", normalized, re.UNICODE)
    return 0 < len(tokens) <= 4


def is_receipt_selection_message(message: str) -> bool:
    """Return true when a message may safely select recent-batch receipts."""
    normalized = _normalized_selector_text(message)
    return is_strict_receipt_selector_message(normalized) or bool(_EXPLICIT_RECEIPT_SELECTION_RE.search(normalized))


def _can_attempt_receipt_selection(message: str) -> bool:
    normalized = _normalized_selector_text(message)
    if is_receipt_selection_message(normalized):
        return True
    if _OTHER_ONE_RE.search(normalized) or _REMAINING_RE.search(normalized):
        return True
    if _ALL_EXCEPT_RE.search(normalized):
        return True
    only_match = _ONLY_SELECTION_RE.search(normalized)
    if only_match:
        tail = str(only_match.group("tail") or "").strip()
        return bool(tail)
    return False


def candidate_label(candidate: SupportReferenceCandidate) -> str:
    if candidate.recipient_label:
        return candidate.recipient_label
    if candidate.recipient_resolved_name:
        return candidate.recipient_resolved_name
    if candidate.recipient_name:
        return candidate.recipient_name
    return candidate.task_type.replace("_", " ").title()


def build_reference_prompt(candidates: list[SupportReferenceCandidate], locale: str) -> str:
    if not candidates:
        return render_message("support.reference.which_transaction", locale)
    lines = [
        render_message("support.reference.ambiguous_header", locale),
        "",
        render_message("support.reference.options_header", locale),
    ]
    for candidate in candidates:
        amount = format_naira(candidate.amount) if isinstance(candidate.amount, (int, float)) else "This transaction"
        lines.append(f"{candidate.ordinal}️⃣ {amount} — {candidate_label(candidate)}")
    lines.extend(["", render_message("query.clarify.reply_number_or_rephrase", locale)])
    return "\n".join(lines)


def build_reference_reminder(candidates: list[SupportReferenceCandidate], locale: str) -> str:
    if not candidates:
        return render_message("support.ask_clarification", locale)
    if len(candidates) == 1:
        return render_message("support.reference.reply_with_one", locale)
    return render_message("support.reference.reply_with_range", locale, {"count": len(candidates)})


def leg_to_candidate(leg: RecentBatchLeg) -> SupportReferenceCandidate | None:
    transaction_id = leg.get("transaction_id")
    if not isinstance(transaction_id, str) or not transaction_id.strip():
        return None
    raw_final_status = str(leg.get("final_status") or "success")
    if raw_final_status == "processing":
        final_status: _FinalStatus = "processing"
    elif raw_final_status == "failed":
        final_status = "failed"
    else:
        final_status = "success"
    return SupportReferenceCandidate(
        transaction_id=transaction_id,
        ordinal=int(leg.get("index") or 0),
        task_type=str(leg.get("task_type") or ""),
        amount=leg.get("amount"),
        recipient_name=leg.get("recipient_name"),
        recipient_resolved_name=leg.get("recipient_resolved_name"),
        recipient_label=leg.get("recipient_label"),
        bank_display=leg.get("bank_display"),
        account_display=leg.get("account_display"),
        final_status=final_status,
        error_message=leg.get("error_message"),
        failure_category=leg.get("failure_category"),
        receipt_allowed=bool(leg.get("receipt_allowed")),
    )


def is_all_receipts_request(message: str) -> bool:
    return bool(_ALL_RECEIPTS_RE.search(message))


def is_both_receipts_request(message: str) -> bool:
    return bool(_BOTH_RECEIPTS_RE.search(message))


def build_batch_receipt_ack(
    *,
    total_jobs: int,
    skipped_failed: int,
    skipped_processing: int,
    skipped_non_transfer: int,
    locale: str,
) -> str:
    base_key: MessageKey = (
        "support.receipt.batch_sending_one" if total_jobs == 1 else "support.receipt.batch_sending_many"
    )
    base = render_message(base_key, locale)
    skipped_parts: list[str] = []
    if skipped_failed:
        skipped_parts.append(render_message("support.receipt.batch_skipped_failed", locale, {"count": skipped_failed}))
    if skipped_processing:
        skipped_parts.append(
            render_message("support.receipt.batch_skipped_pending", locale, {"count": skipped_processing})
        )
    if skipped_non_transfer:
        skipped_parts.append(
            render_message("support.receipt.batch_skipped_non_transfer", locale, {"count": skipped_non_transfer})
        )
    if not skipped_parts:
        return base
    skipped_text = ", ".join(skipped_parts)
    skipped_count = sum((skipped_failed, skipped_processing, skipped_non_transfer))
    return render_message(
        "support.receipt.batch_skipped_items",
        locale,
        {"base": base, "skipped": skipped_text, "item_suffix": "s" if skipped_count != 1 else ""},
    )


def batch_receipt_exhausted_message(locale: str) -> str:
    return render_message("support.receipt.batch_exhausted", locale)


def eligible_receipt_candidates(candidates: list[SupportReferenceCandidate]) -> list[SupportReferenceCandidate]:
    return [candidate for candidate in candidates if candidate.task_type == "transfer" and candidate.receipt_allowed]


def build_receipt_thread_state(
    *,
    async_group_id: str,
    candidates: list[SupportReferenceCandidate],
    locale: str,
    served_transaction_ids: list[str] | None = None,
    last_selector_result_ids: list[str] | None = None,
    last_served_transaction_ids: list[str] | None = None,
) -> ReceiptBatchThreadState:
    served_ids = list(dict.fromkeys(served_transaction_ids or []))
    eligible_ids = [candidate.transaction_id for candidate in eligible_receipt_candidates(candidates)]
    remaining_ids = [transaction_id for transaction_id in eligible_ids if transaction_id not in served_ids]
    return ReceiptBatchThreadState(
        async_group_id=async_group_id,
        candidates=candidates,
        served_transaction_ids=served_ids,
        remaining_transaction_ids=remaining_ids,
        last_selector_result_ids=list(dict.fromkeys(last_selector_result_ids or [])),
        last_served_transaction_ids=list(dict.fromkeys(last_served_transaction_ids or [])),
        reminder=build_reference_reminder(eligible_receipt_candidates(candidates), locale),
        expires_at_ts=time.time() + EPHEMERAL_CONTEXT_TTL_SECONDS,
    )


def selector_from_refs(
    *,
    message: str,
    include_candidates: list[SupportReferenceCandidate],
    exclude_candidates: list[SupportReferenceCandidate] | None = None,
    selection_mode: _SelectionMode = "subset",
    wants_remaining: bool = False,
) -> ReceiptBatchSelection:
    include_refs: list[ReceiptBatchSelectionRef] = []
    exclude_refs: list[ReceiptBatchSelectionRef] = []
    for candidate in include_candidates:
        include_refs.append(
            ReceiptBatchSelectionRef(
                kind="ordinal",
                ordinal=candidate.ordinal,
                recipient_label=candidate_label(candidate),
                amount=candidate.amount,
            )
        )
    for candidate in exclude_candidates or []:
        exclude_refs.append(
            ReceiptBatchSelectionRef(
                kind="ordinal",
                ordinal=candidate.ordinal,
                recipient_label=candidate_label(candidate),
                amount=candidate.amount,
            )
        )
    return ReceiptBatchSelection(
        selection_mode=selection_mode,
        include_refs=include_refs,
        exclude_refs=exclude_refs,
        wants_remaining=wants_remaining,
    )


def match_reference_candidates(
    *,
    message: str,
    candidates: list[SupportReferenceCandidate],
) -> list[SupportReferenceCandidate]:
    if not message.strip():
        return []
    matches: dict[str, SupportReferenceCandidate] = {}
    ordinals = ordinals_from_message(message)
    if ordinals:
        if -1 in ordinals and candidates:
            matches[max(candidates, key=lambda candidate: candidate.ordinal).transaction_id] = max(
                candidates, key=lambda candidate: candidate.ordinal
            )
        for ordinal in ordinals:
            if ordinal == -1:
                continue
            for candidate in candidates:
                if candidate.ordinal == ordinal:
                    matches[candidate.transaction_id] = candidate
    normalized_message = normalize_match_text(message)
    for amount in amounts_from_message(message):
        for candidate in candidates:
            if candidate.amount is not None and abs(candidate.amount - amount) < 1:
                matches[candidate.transaction_id] = candidate

    if normalized_message:
        for candidate in candidates:
            labels = {
                normalize_match_text(candidate.recipient_label),
                normalize_match_text(candidate.recipient_name),
                normalize_match_text(candidate.recipient_resolved_name),
            }
            labels = {label for label in labels if label}
            if any(label in normalized_message or normalized_message in label for label in labels):
                matches[candidate.transaction_id] = candidate

    return sorted(matches.values(), key=lambda candidate: candidate.ordinal)


def pending_reference_state(
    *,
    candidates: list[SupportReferenceCandidate],
    locale: str,
    intent: SupportIntent | None = None,
) -> PendingReferenceState:
    return PendingReferenceState(
        source="recent_batch",
        candidates=candidates,
        reminder=build_reference_reminder(candidates, locale),
        intent=intent.value if intent is not None else None,
        expires_at_ts=time.time() + EPHEMERAL_CONTEXT_TTL_SECONDS,
    )


def select_recent_batch_candidates(
    *,
    message: str,
    candidates: list[SupportReferenceCandidate],
    thread_state: ReceiptBatchThreadState | None,
    locale: str,
) -> tuple[
    list[SupportReferenceCandidate],
    ReceiptBatchSelection | None,
    str | None,
    list[SupportReferenceCandidate] | None,
]:
    eligible_candidates = eligible_receipt_candidates(candidates)
    remaining_candidates = [
        candidate
        for candidate in eligible_candidates
        if not thread_state or candidate.transaction_id in thread_state.remaining_transaction_ids
    ]
    normalized_message = message.strip()
    if not _can_attempt_receipt_selection(normalized_message):
        return [], None, None, None

    all_except_match = _ALL_EXCEPT_RE.search(normalized_message)

    if _OTHER_ONE_RE.search(normalized_message):
        if len(remaining_candidates) == 1:
            return (
                remaining_candidates,
                selector_from_refs(
                    message=message,
                    include_candidates=remaining_candidates,
                    selection_mode="remainder",
                    wants_remaining=True,
                ),
                None,
                None,
            )
        if remaining_candidates:
            return [], None, None, remaining_candidates
        return [], None, batch_receipt_exhausted_message(locale), None

    if _REMAINING_RE.search(normalized_message):
        if remaining_candidates:
            return (
                remaining_candidates,
                selector_from_refs(
                    message=message,
                    include_candidates=remaining_candidates,
                    selection_mode="remainder",
                    wants_remaining=True,
                ),
                None,
                None,
            )
        return [], None, batch_receipt_exhausted_message(locale), None

    if all_except_match:
        base_candidates = candidates
        if is_both_receipts_request(normalized_message):
            if len(eligible_candidates) != 2:
                return [], None, None, eligible_candidates
        excluded = match_reference_candidates(
            message=str(all_except_match.group("tail") or "").strip(),
            candidates=base_candidates,
        )
        if not excluded:
            return [], None, None, eligible_candidates
        selected = [candidate for candidate in base_candidates if candidate not in excluded]
        if not selected and excluded:
            return [], None, batch_receipt_exhausted_message(locale), None
        return (
            selected,
            selector_from_refs(
                message=message,
                include_candidates=selected,
                exclude_candidates=excluded,
                selection_mode="all",
            ),
            None,
            None,
        )

    if is_all_receipts_request(normalized_message):
        selected = list(candidates)
        return (
            selected,
            selector_from_refs(
                message=message,
                include_candidates=selected,
                selection_mode="all",
            ),
            None,
            None,
        )

    if is_both_receipts_request(normalized_message):
        if len(eligible_candidates) == 2:
            selected = list(candidates)
            return (
                selected,
                selector_from_refs(
                    message=message,
                    include_candidates=selected,
                    selection_mode="all",
                ),
                None,
                None,
            )
        return [], None, None, eligible_candidates

    only_match = _ONLY_SELECTION_RE.search(normalized_message)
    reference_message = str(only_match.group("tail") or "").strip() if only_match else normalized_message
    matched = match_reference_candidates(message=reference_message, candidates=eligible_candidates)
    if matched:
        return (
            matched,
            selector_from_refs(message=message, include_candidates=matched, selection_mode="subset"),
            None,
            None,
        )

    if only_match:
        return [], None, None, eligible_candidates
    return [], None, None, None


__all__ = [
    "amounts_from_message",
    "batch_receipt_exhausted_message",
    "build_batch_receipt_ack",
    "build_receipt_thread_state",
    "build_reference_prompt",
    "build_reference_reminder",
    "candidate_label",
    "eligible_receipt_candidates",
    "is_all_receipts_request",
    "is_amount_only_selector_message",
    "is_both_receipts_request",
    "is_receipt_selection_message",
    "is_strict_receipt_selector_message",
    "is_strict_reference_selector_message",
    "leg_to_candidate",
    "match_reference_candidates",
    "normalize_match_text",
    "ordinal_from_message",
    "ordinals_from_message",
    "pending_reference_state",
    "select_recent_batch_candidates",
    "selector_from_refs",
]
