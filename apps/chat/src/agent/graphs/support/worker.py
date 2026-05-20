"""Support Worker.

Stateless worker for Support tasks using LangGraph.
"""

import re
import uuid
from typing import Any

from apps.chat.src.agent.graphs.support.capabilities import SUPPORT_LIMITS, SupportAction
from apps.chat.src.agent.graphs.support.classifier import SupportClassifier
from apps.chat.src.agent.graphs.support.context_manager import SupportContextManager
from apps.chat.src.agent.graphs.support.diagnostic_agent import (
    SupportDiagnosticAgent,
    build_support_diagnostic_context,
)
from apps.chat.src.agent.graphs.support.handlers import (
    handle_failure_reason,
    handle_fraud,
    handle_pending,
    handle_receipt_request,
    handle_retry,
    handle_reversal_status,
    handle_ticket_status,
    handle_transfer_status,
    handle_wrong_debit,
)
from apps.chat.src.agent.graphs.support.handlers.escalation import handle_escalation
from apps.chat.src.agent.graphs.support.handlers.retry import build_retry_quoted_data
from apps.chat.src.agent.graphs.support.micro_resolver import (
    NextStep,
)
from apps.chat.src.agent.graphs.support.micro_resolver import (
    resolve as micro_resolve,
)
from apps.chat.src.agent.graphs.support.models import (
    PendingReferenceState,
    ReceiptBatchSelection,
    ReceiptBatchSelectionRef,
    ReceiptBatchThreadState,
    SupportDiagnosticAction,
    SupportDiagnosticDecision,
    SupportExtractionResult,
    SupportIntent,
    SupportReferenceCandidate,
    SupportResponse,
    TransactionReference,
)
from apps.chat.src.agent.graphs.support.resolver import TransactionResolver
from apps.chat.src.agent.orchestrator.models.domain import SupportOutcome, SupportResult
from apps.chat.src.agent.shared.routing_signals import looks_like_transaction_replay_modifier_request
from shared.config.settings import settings
from shared.formatters.currency import format_naira
from shared.i18n import LocaleManager, render_message
from shared.policy.adapters import is_capability_supported
from shared.policy.service import capability_block_message
from shared.queue.models import ReceiptJobPayload, ReceiptTransferData
from shared.services.async_completion import RecentBatchLeg, get_recent_batch_reference
from shared.services.ticket_service import TicketService
from shared.utils.logging import get_logger

logger = get_logger(__name__)
_ACK_ONLY_RE = re.compile(r"^(ok(?:ay)?|alright|yes|yeah|yep|sure)\.?$", re.IGNORECASE)
_ORDINAL_RE = re.compile(r"\b(?:(first|second|third|fourth|fifth|last)|([1-5])(?:st|nd|rd|th)?)\b", re.IGNORECASE)
_AMOUNT_RE = re.compile(r"(?:₦|ngn)?\s*(\d[\d,]*(?:\.\d+)?)\s*([kKhH]?)")
_ALL_RECEIPTS_RE = re.compile(r"\b(?:all|every)\b.*\breceipts?\b|\breceipts?\b.*\b(?:all|every)\b", re.IGNORECASE)
_BOTH_RECEIPTS_RE = re.compile(r"\b(?:both|the two(?:\s+of\s+them)?|two of them)\b", re.IGNORECASE)
_OTHER_ONE_RE = re.compile(r"\b(?:the other one|other one|the other)\b", re.IGNORECASE)
_REMAINING_RE = re.compile(r"\b(?:the remaining ones?|remaining ones?|the rest|rest of them)\b", re.IGNORECASE)
_ALL_EXCEPT_RE = re.compile(r"\b(?:all|every|both)\b.*?\b(?:except|excluding|but not|apart from)\b(?P<tail>.+)$", re.IGNORECASE)
_ONLY_SELECTION_RE = re.compile(r"\b(?:only|just)\b(?P<tail>.+)$", re.IGNORECASE)
_TICKET_CODE_RE = re.compile(r"\b(SUP-\d{8}-\d{4})\b", re.IGNORECASE)
_RECENT_TRANSACTION_STATUS_ASSERTION_RE = re.compile(
    r"\b(?:my|the|this|that)?\s*(?:last|latest|most\s+recent|recent)\s+"
    r"(?:transaction|transfer|payment)\s+"
    r"(?:failed|fail(?:ed)?|declined|rejected|is\s+pending|is\s+processing|is\s+stuck|"
    r"was\s+pending|was\s+processing|was\s+stuck|didn['’]?t\s+go\s+through)\b",
    re.IGNORECASE,
)
_RECENT_TRANSACTION_REFERENCE_RE = re.compile(
    r"^\s*(?:my|the|this|that)?\s*(?:last|latest|most\s+recent|recent)\s+"
    r"(?:transaction|transfer|payment)\s*$"
    r"|^\s*(?:the\s+)?(?:last|latest|recent)\s+one\s*$"
    r"|^\s*(?:it|this|that|that\s+one)\s*$",
    re.IGNORECASE,
)
_DETAIL_FOLLOWUP_RE = re.compile(
    r"\b(?:details?|full\s+details?|more\s+info(?:rmation)?|show\s+(?:me\s+)?(?:the\s+)?details?)\b",
    re.IGNORECASE,
)
_STATUS_FOLLOWUP_RE = re.compile(
    r"\b(?:status|state|what\s+happened|did\s+it\s+(?:go\s+through|fail|succeed))\b",
    re.IGNORECASE,
)
_RETRY_FOLLOWUP_RE = re.compile(r"\b(?:retry|try\s+again|resend|send\s+again)\b", re.IGNORECASE)


def _support_identity(context: dict[str, Any]) -> str | None:
    for key in ("channel_identity", "phone_number", "user_id"):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _ticket_code_from_message(message: str) -> str | None:
    match = _TICKET_CODE_RE.search(message)
    if match is None:
        return None
    return match.group(1).upper()


def _normalize_match_text(value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.strip().lower()).strip()


def _parse_amount_reference(message: str) -> float | None:
    match = _AMOUNT_RE.search(message)
    if match is None:
        return None
    try:
        amount = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    suffix = (match.group(2) or "").lower()
    if suffix == "k":
        amount *= 1000.0
    elif suffix == "h":
        amount *= 100.0
    return amount if amount > 0 else None


def _ordinal_from_message(message: str) -> int | None:
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


def _ordinals_from_message(message: str) -> list[int]:
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


def _amounts_from_message(message: str) -> list[float]:
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


def _candidate_label(candidate: SupportReferenceCandidate) -> str:
    if candidate.recipient_label:
        return candidate.recipient_label
    if candidate.recipient_resolved_name:
        return candidate.recipient_resolved_name
    if candidate.recipient_name:
        return candidate.recipient_name
    return candidate.task_type.replace("_", " ").title()


def _build_reference_prompt(candidates: list[SupportReferenceCandidate]) -> str:
    if not candidates:
        return "Which transaction do you mean?"
    lines = ["I'm not sure which one you mean (Which transaction).", "", "Are you referring to:"]
    for candidate in candidates:
        amount = format_naira(candidate.amount) if isinstance(candidate.amount, (int, float)) else "This transaction"
        lines.append(f"{candidate.ordinal}️⃣ {amount} — {_candidate_label(candidate)}")
    lines.extend(["", "Reply with the number or rephrase."])
    return "\n".join(lines)


def _build_reference_reminder(candidates: list[SupportReferenceCandidate]) -> str:
    if not candidates:
        return render_message("support.ask_clarification", "en")
    if len(candidates) == 1:
        return "Reply with 1."
    return f"Reply with 1 or {len(candidates)}."


def _leg_to_candidate(leg: RecentBatchLeg) -> SupportReferenceCandidate | None:
    transaction_id = leg.get("transaction_id")
    if not isinstance(transaction_id, str) or not transaction_id.strip():
        return None
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
        final_status=str(leg.get("final_status") or "success"),
        error_message=leg.get("error_message"),
        failure_category=leg.get("failure_category"),
        receipt_allowed=bool(leg.get("receipt_allowed")),
    )


def _is_all_receipts_request(message: str) -> bool:
    return bool(_ALL_RECEIPTS_RE.search(message))


def _is_both_receipts_request(message: str) -> bool:
    return bool(_BOTH_RECEIPTS_RE.search(message))


def _build_batch_receipt_ack(
    *,
    total_jobs: int,
    skipped_failed: int,
    skipped_processing: int,
    skipped_non_transfer: int,
) -> str:
    base = "I'm sending the receipt now." if total_jobs == 1 else "I'm sending the receipts for the successful transfers now."
    skipped_parts: list[str] = []
    if skipped_failed:
        skipped_parts.append(f"{skipped_failed} failed")
    if skipped_processing:
        skipped_parts.append(f"{skipped_processing} pending")
    if skipped_non_transfer:
        skipped_parts.append(f"{skipped_non_transfer} non-transfer")
    if not skipped_parts:
        return base
    skipped_text = ", ".join(skipped_parts)
    return f"{base} I skipped {skipped_text} item{'s' if sum((skipped_failed, skipped_processing, skipped_non_transfer)) != 1 else ''}."


def _batch_receipt_exhausted_message() -> str:
    return "I've already sent the available receipts for that batch."


class SupportWorker:
    """Stateless worker for Support tasks."""

    def __init__(
        self,
        llm: Any,
        transaction_repo: Any,
        actionable_message_repo: Any,
        redis_client: Any,
        ticket_service: TicketService | None = None,
        bank_transaction_repo: Any | None = None,
    ) -> None:
        self.llm = llm
        self.classifier = SupportClassifier(llm)
        self.resolver = TransactionResolver(transaction_repo, actionable_message_repo, bank_transaction_repo)
        self.context_manager = SupportContextManager(redis_client)
        self._ticket_service = ticket_service
        self._diagnostic_agent: SupportDiagnosticAgent | None = None

    def _build_retry_handoff(self, response: SupportResponse, intent: SupportIntent) -> dict[str, Any] | None:
        if intent != SupportIntent.RETRY_TRANSFER or not response.offer_retry:
            return response.handoff
        transaction = response.transaction_data
        if not isinstance(transaction, dict):
            return response.handoff
        quoted_data = build_retry_quoted_data(transaction)
        return {
            "type": "retry_transfer",
            "payload": quoted_data.get("data", {}),
            "quoted_data": quoted_data,
            "requires_confirmation": True,
        }

    def _result_from_support_response(
        self,
        response: SupportResponse | None,
        *,
        intent: SupportIntent,
        locale: str,
    ) -> SupportResult:
        final_msg = response.message if response else render_message("support.unable_to_process", locale)
        handoff = self._build_retry_handoff(response, intent) if response else None
        return SupportResult(
            outcome=SupportOutcome.OK,
            response=final_msg,
            final_message=final_msg,
            handoff=handoff,
        )

    def _build_receipt_transfer_data(self, transaction: dict[str, Any], *, locale: str) -> ReceiptTransferData:
        source_account_name = _optional_text(transaction.get("source_account_name"))
        return {
            "amount": transaction.get("amount"),
            "source": {
                "name": transaction.get("source_bank_name"),
                "account_name": source_account_name or render_message("query.receipt.user_account", locale),
                "account_number": transaction.get("source_account_number"),
            },
            "recipient": {
                "name": transaction.get("recipient_name"),
                "account_number": transaction.get("recipient_account_number"),
                "bank_name": transaction.get("recipient_bank_name"),
            },
            "narration": transaction.get("narration"),
            "session_id": str(transaction.get("transaction_id") or transaction.get("id") or ""),
        }

    def _build_receipt_job(
        self,
        *,
        transaction: dict[str, Any],
        context: dict[str, Any],
        locale: str,
    ) -> ReceiptJobPayload:
        phone_number = str(context.get("phone_number") or "")
        channel = str(context.get("channel") or "whatsapp")
        channel_identity = context.get("channel_identity")
        transaction_reference = str(transaction.get("transaction_id") or transaction.get("id") or "")
        return {
            "phone_number": phone_number,
            "channel": channel,
            "channel_identity": str(channel_identity) if isinstance(channel_identity, str) and channel_identity.strip() else None,
            "transfer_data": self._build_receipt_transfer_data(transaction, locale=locale),
            "transaction_reference": transaction_reference or None,
            "signal_key": f"receipt:{uuid.uuid4()}",
        }

    def _receipt_unavailable_result(self, *, transaction: dict[str, Any], locale: str) -> SupportResult:
        response = render_message(
            "support.receipt.unavailable_for_status",
            locale,
            {"status": str(transaction.get("status", "unknown") or "unknown").strip().lower()},
        )
        return SupportResult(
            outcome=SupportOutcome.OK,
            response=response,
            final_message=response,
        )

    def _receipt_only_transfer_result(self, *, transaction_type: str, locale: str) -> SupportResult:
        response = render_message(
            "query.receipt.only_transfer",
            locale,
            {"transaction_type": transaction_type.replace("_", " ") or "transaction"},
        )
        return SupportResult(
            outcome=SupportOutcome.OK,
            response=response,
            final_message=response,
        )

    def _build_receipt_result(
        self,
        *,
        transaction: dict[str, Any],
        context: dict[str, Any],
        locale: str,
    ) -> SupportResult:
        transaction_type = str(transaction.get("transaction_type") or "").strip().lower()
        if transaction_type != "transfer":
            return self._receipt_only_transfer_result(transaction_type=transaction_type, locale=locale)

        status = str(transaction.get("status", "unknown") or "unknown").strip().lower()
        if status not in {"success", "successful"}:
            return self._receipt_unavailable_result(transaction=transaction, locale=locale)

        response = render_message("query.receipt.generating", locale)
        return SupportResult(
            outcome=SupportOutcome.OK,
            response=response,
            final_message=response,
            receipt_jobs=[self._build_receipt_job(transaction=transaction, context=context, locale=locale)],
        )

    async def _load_transaction_dict(self, transaction_id: str) -> dict[str, Any] | None:
        tx = await self.resolver.tx_repo.get_by_id(transaction_id)
        if not tx:
            return None
        return self.resolver.transaction_to_dict(tx)

    def _eligible_receipt_candidates(
        self, candidates: list[SupportReferenceCandidate]
    ) -> list[SupportReferenceCandidate]:
        return [
            candidate
            for candidate in candidates
            if candidate.task_type == "transfer" and candidate.receipt_allowed
        ]

    def _candidate_by_id(
        self,
        candidates: list[SupportReferenceCandidate],
        transaction_id: str,
    ) -> SupportReferenceCandidate | None:
        for candidate in candidates:
            if candidate.transaction_id == transaction_id:
                return candidate
        return None

    def _build_receipt_thread_state(
        self,
        *,
        async_group_id: str,
        candidates: list[SupportReferenceCandidate],
        served_transaction_ids: list[str] | None = None,
        last_selector_result_ids: list[str] | None = None,
        last_served_transaction_ids: list[str] | None = None,
    ) -> ReceiptBatchThreadState:
        served_ids = list(dict.fromkeys(served_transaction_ids or []))
        eligible_ids = [candidate.transaction_id for candidate in self._eligible_receipt_candidates(candidates)]
        remaining_ids = [transaction_id for transaction_id in eligible_ids if transaction_id not in served_ids]
        return ReceiptBatchThreadState(
            async_group_id=async_group_id,
            candidates=candidates,
            served_transaction_ids=served_ids,
            remaining_transaction_ids=remaining_ids,
            last_selector_result_ids=list(dict.fromkeys(last_selector_result_ids or [])),
            last_served_transaction_ids=list(dict.fromkeys(last_served_transaction_ids or [])),
            reminder=_build_reference_reminder(self._eligible_receipt_candidates(candidates)),
        )

    async def _save_receipt_thread_state(
        self,
        *,
        user_id: str,
        support_ctx: Any,
        thread_state: ReceiptBatchThreadState,
    ) -> None:
        support_ctx.receipt_thread_state = thread_state
        await self.context_manager.save(user_id, support_ctx)

    async def _clear_receipt_thread_state(self, *, user_id: str, support_ctx: Any) -> None:
        if support_ctx.receipt_thread_state is None:
            return
        support_ctx.receipt_thread_state = None
        await self.context_manager.save(user_id, support_ctx)

    def _selector_from_refs(
        self,
        *,
        message: str,
        include_candidates: list[SupportReferenceCandidate],
        exclude_candidates: list[SupportReferenceCandidate] | None = None,
        selection_mode: str = "subset",
        wants_remaining: bool = False,
    ) -> ReceiptBatchSelection:
        include_refs: list[ReceiptBatchSelectionRef] = []
        exclude_refs: list[ReceiptBatchSelectionRef] = []
        for candidate in include_candidates:
            include_refs.append(
                ReceiptBatchSelectionRef(
                    kind="ordinal",
                    ordinal=candidate.ordinal,
                    recipient_label=_candidate_label(candidate),
                    amount=candidate.amount,
                )
            )
        for candidate in exclude_candidates or []:
            exclude_refs.append(
                ReceiptBatchSelectionRef(
                    kind="ordinal",
                    ordinal=candidate.ordinal,
                    recipient_label=_candidate_label(candidate),
                    amount=candidate.amount,
                )
            )
        return ReceiptBatchSelection(
            selection_mode=selection_mode, include_refs=include_refs, exclude_refs=exclude_refs, wants_remaining=wants_remaining
        )

    def _match_reference_candidates(
        self,
        *,
        message: str,
        candidates: list[SupportReferenceCandidate],
    ) -> list[SupportReferenceCandidate]:
        if not message.strip():
            return []
        matches: dict[str, SupportReferenceCandidate] = {}
        ordinals = _ordinals_from_message(message)
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
        normalized_message = _normalize_match_text(message)
        for amount in _amounts_from_message(message):
            for candidate in candidates:
                if candidate.amount is not None and abs(candidate.amount - amount) < 1:
                    matches[candidate.transaction_id] = candidate

        if normalized_message:
            for candidate in candidates:
                labels = {
                    _normalize_match_text(candidate.recipient_label),
                    _normalize_match_text(candidate.recipient_name),
                    _normalize_match_text(candidate.recipient_resolved_name),
                }
                labels = {label for label in labels if label}
                if any(label in normalized_message or normalized_message in label for label in labels):
                    matches[candidate.transaction_id] = candidate

        return sorted(matches.values(), key=lambda candidate: candidate.ordinal)

    async def _save_pending_reference(
        self,
        *,
        user_id: str,
        support_ctx: Any,
        candidates: list[SupportReferenceCandidate],
        locale: str,
        intent: SupportIntent | None = None,
    ) -> None:
        reminder = _build_reference_reminder(candidates)
        support_ctx.pending_reference = PendingReferenceState(
            source="recent_batch",
            candidates=candidates,
            reminder=reminder if locale == "en" else reminder,
            intent=intent.value if intent is not None else None,
        )
        await self.context_manager.save(user_id, support_ctx)

    def _select_recent_batch_candidates(
        self,
        *,
        message: str,
        candidates: list[SupportReferenceCandidate],
        thread_state: ReceiptBatchThreadState | None,
    ) -> tuple[list[SupportReferenceCandidate], ReceiptBatchSelection | None, str | None, list[SupportReferenceCandidate] | None]:
        eligible_candidates = self._eligible_receipt_candidates(candidates)
        remaining_candidates = [
            candidate
            for candidate in eligible_candidates
            if not thread_state or candidate.transaction_id in thread_state.remaining_transaction_ids
        ]
        normalized_message = message.strip()
        all_except_match = _ALL_EXCEPT_RE.search(normalized_message)

        if _OTHER_ONE_RE.search(normalized_message):
            if len(remaining_candidates) == 1:
                return (
                    remaining_candidates,
                    self._selector_from_refs(
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
            return [], None, _batch_receipt_exhausted_message(), None

        if _REMAINING_RE.search(normalized_message):
            if remaining_candidates:
                return (
                    remaining_candidates,
                    self._selector_from_refs(
                        message=message,
                        include_candidates=remaining_candidates,
                        selection_mode="remainder",
                        wants_remaining=True,
                    ),
                    None,
                    None,
                )
            return [], None, _batch_receipt_exhausted_message(), None

        if all_except_match:
            base_candidates = candidates
            if _is_both_receipts_request(normalized_message):
                if len(eligible_candidates) != 2:
                    return [], None, None, eligible_candidates
            excluded = self._match_reference_candidates(
                message=str(all_except_match.group("tail") or "").strip(),
                candidates=base_candidates,
            )
            if not excluded:
                return [], None, None, eligible_candidates
            selected = [candidate for candidate in base_candidates if candidate not in excluded]
            if not selected and excluded:
                return [], None, _batch_receipt_exhausted_message(), None
            return (
                selected,
                self._selector_from_refs(
                    message=message,
                    include_candidates=selected,
                    exclude_candidates=excluded,
                    selection_mode="all",
                ),
                None,
                None,
            )

        if _is_all_receipts_request(normalized_message):
            selected = list(candidates)
            return (
                selected,
                self._selector_from_refs(
                    message=message,
                    include_candidates=selected,
                    selection_mode="all",
                ),
                None,
                None,
            )

        if _is_both_receipts_request(normalized_message):
            if len(eligible_candidates) == 2:
                selected = list(candidates)
                return (
                    selected,
                    self._selector_from_refs(
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
        matched = self._match_reference_candidates(message=reference_message, candidates=eligible_candidates)
        if matched:
            return (
                matched,
                self._selector_from_refs(message=message, include_candidates=matched, selection_mode="subset"),
                None,
                None,
            )

        if only_match:
            return [], None, None, eligible_candidates
        return [], None, None, None

    async def _build_receipt_jobs_for_candidates(
        self,
        *,
        selected: list[SupportReferenceCandidate],
        candidates: list[SupportReferenceCandidate],
        context: dict[str, Any],
        locale: str,
    ) -> tuple[list[dict[str, Any]], int, int, int]:
        selected_ids = {candidate.transaction_id for candidate in selected}
        jobs: list[dict[str, Any]] = []
        skipped_failed = 0
        skipped_processing = 0
        skipped_non_transfer = 0
        for candidate in sorted(candidates, key=lambda item: item.ordinal):
            if candidate.transaction_id not in selected_ids:
                continue
            if candidate.task_type != "transfer":
                skipped_non_transfer += 1
                continue
            if not candidate.receipt_allowed:
                if candidate.final_status == "failed":
                    skipped_failed += 1
                else:
                    skipped_processing += 1
                continue
            transaction = await self._load_transaction_dict(candidate.transaction_id)
            if transaction is None:
                skipped_failed += 1
                continue
            jobs.append(self._build_receipt_job(transaction=transaction, context=context, locale=locale))
        return jobs, skipped_failed, skipped_processing, skipped_non_transfer

    async def _clear_pending_reference(self, *, user_id: str, support_ctx: Any) -> None:
        if support_ctx.pending_reference is None:
            return
        support_ctx.pending_reference = None
        await self.context_manager.save(user_id, support_ctx)

    def _get_diagnostic_agent(self) -> SupportDiagnosticAgent | None:
        if self._diagnostic_agent is not None:
            return self._diagnostic_agent
        try:
            self._diagnostic_agent = SupportDiagnosticAgent(self.llm)
        except Exception as exc:
            logger.info("support_diagnostic_agent_unavailable", error=str(exc))
            return None
        return self._diagnostic_agent

    @staticmethod
    def _support_capability_summary() -> dict[str, bool]:
        return {
            action.value: is_capability_supported(domain="support", action=action.value)
            for action in SupportAction
        }

    async def _recent_diagnostic_candidates(self, context: dict[str, Any]) -> list[SupportReferenceCandidate]:
        identity = _support_identity(context)
        recent_batch = await get_recent_batch_reference(self.context_manager.redis, identity=identity)
        if recent_batch is None:
            return []
        candidates = [candidate for leg in recent_batch["legs"] if (candidate := _leg_to_candidate(leg)) is not None]
        return [candidate for candidate in candidates if candidate.final_status in {"failed", "processing"}]

    @staticmethod
    def _diagnostic_required_actions(decision: SupportDiagnosticDecision) -> list[str]:
        required: list[str] = list(decision.required_actions)
        defaults: dict[SupportDiagnosticAction, list[str]] = {
            SupportDiagnosticAction.ASK_REFERENCE: [SupportAction.COLLECT_DETAILS.value],
            SupportDiagnosticAction.ASK_CLARIFICATION: [SupportAction.COLLECT_DETAILS.value],
            SupportDiagnosticAction.LOOKUP_TRANSACTION: [
                SupportAction.LOOKUP_TRANSACTION.value,
                SupportAction.EXPLAIN_STATUS.value,
            ],
            SupportDiagnosticAction.EXPLAIN_TRANSACTION: [
                SupportAction.LOOKUP_TRANSACTION.value,
                SupportAction.EXPLAIN_STATUS.value,
            ],
            SupportDiagnosticAction.LOOKUP_TICKET: [SupportAction.LOOKUP_TICKET.value],
            SupportDiagnosticAction.CREATE_TICKET: [SupportAction.CREATE_TICKET.value],
            SupportDiagnosticAction.ESCALATE_TICKET: [
                SupportAction.CREATE_TICKET.value,
                SupportAction.ESCALATE.value,
            ],
            SupportDiagnosticAction.PREPARE_RETRY_HANDOFF: [
                SupportAction.LOOKUP_TRANSACTION.value,
                SupportAction.RETRY_PAYOUT.value,
            ],
        }
        for action in defaults.get(decision.next_action, []):
            if action not in required:
                required.append(action)
        if decision.intent in {SupportIntent.FRAUD_REPORT, SupportIntent.HUMAN_HANDOFF}:
            for action in (SupportAction.CREATE_TICKET.value, SupportAction.ESCALATE.value):
                if action not in required:
                    required.append(action)
        return required

    @staticmethod
    def _policy_block_for_actions(actions: list[str], *, locale: str) -> str | None:
        for action in actions:
            if message := capability_block_message(domain="support", action=action, locale=locale):
                return message
        return None

    @staticmethod
    def _recent_status_priority(intent: SupportIntent) -> list[str] | None:
        if intent in {SupportIntent.FAILED_TRANSFER, SupportIntent.RETRY_TRANSFER}:
            return ["failed", "processing", "pending"]
        if intent == SupportIntent.PENDING_TRANSFER:
            return ["pending", "processing", "failed"]
        return None

    @staticmethod
    def _should_verify_latest_transaction_status(intent: SupportIntent, message: str) -> bool:
        if intent not in {
            SupportIntent.FAILED_TRANSFER,
            SupportIntent.PENDING_TRANSFER,
            SupportIntent.GENERAL_TX_ISSUE,
            SupportIntent.TRANSFER_STATUS,
        }:
            return False
        return bool(_RECENT_TRANSACTION_STATUS_ASSERTION_RE.search(message))

    @staticmethod
    def _is_recent_transaction_reference(message: str) -> bool:
        return bool(_RECENT_TRANSACTION_REFERENCE_RE.search(message or ""))

    def _contextual_followup_reference(
        self,
        *,
        support_ctx: Any,
        message: str,
    ) -> tuple[SupportIntent | None, TransactionReference | None]:
        last_ref = str(getattr(support_ctx, "last_transaction_ref", "") or "").strip()
        if last_ref:
            if _RETRY_FOLLOWUP_RE.search(message) and not looks_like_transaction_replay_modifier_request(message):
                return SupportIntent.RETRY_TRANSFER, TransactionReference(transaction_id=last_ref)
            if _DETAIL_FOLLOWUP_RE.search(message) or _STATUS_FOLLOWUP_RE.search(message):
                return SupportIntent.TRANSFER_STATUS, TransactionReference(transaction_id=last_ref)

        pending_reference = getattr(support_ctx, "pending_reference", None)
        asked_for_reference = getattr(support_ctx, "last_support_step", None) == "asked_for_reference"
        if (pending_reference is not None or asked_for_reference) and self._is_recent_transaction_reference(message):
            fallback_intent = getattr(support_ctx, "last_issue_intent", None) or SupportIntent.GENERAL_TX_ISSUE
            return fallback_intent, TransactionReference(use_recent=True)

        return None, None

    def _diagnostic_has_transaction_grounding(
        self,
        *,
        transaction: dict[str, Any] | None,
        tx_ref: TransactionReference | None,
        quoted_message_id: str | None,
    ) -> bool:
        if isinstance(transaction, dict):
            return True
        if quoted_message_id:
            return True
        if tx_ref is None:
            return False
        if tx_ref.transaction_id or tx_ref.use_quoted or tx_ref.use_recent:
            return True
        return self.resolver._has_explicit_ref(tx_ref)  # type: ignore[attr-defined]

    async def _diagnostic_route(
        self,
        *,
        support_ctx: Any,
        context: dict[str, Any],
        message: str,
        locale: str,
        intent: SupportIntent,
        classification: Any,
        extraction: SupportExtractionResult,
        tx_ref: TransactionReference | None,
        resolved_tx: dict[str, Any] | None,
        quoted_message_id: str | None,
    ) -> dict[str, Any] | None:
        if not settings.enable_support_diagnostic_agent:
            return None
        agent = self._get_diagnostic_agent()
        if agent is None:
            return None

        recent_candidates = await self._recent_diagnostic_candidates(context)
        diagnostic_context = build_support_diagnostic_context(
            message=message,
            locale=locale,
            support_context=support_ctx,
            intent=intent,
            classification=classification,
            transaction=resolved_tx,
            recent_candidates=recent_candidates,
            ticket_code=_ticket_code_from_message(message),
            latest_ticket_id=support_ctx.last_ticket_id,
            capability_summary=self._support_capability_summary(),
        )
        try:
            decision = await agent.decide(diagnostic_context)
        except Exception as exc:
            logger.info("support_diagnostic_agent_failed", error=str(exc))
            return None

        logger.info(
            "support_diagnostic_decision",
            intent=decision.intent.value,
            next_action=decision.next_action.value,
            confidence=decision.confidence,
        )
        if decision.confidence < 0.65:
            return None
        if decision.next_action == SupportDiagnosticAction.FALLBACK_MICRO_RESOLVER:
            return None

        required_actions = self._diagnostic_required_actions(decision)
        if block_message := self._policy_block_for_actions(required_actions, locale=locale):
            return {
                "result": SupportResult(
                    outcome=SupportOutcome.OK,
                    response=block_message,
                    final_message=block_message,
                )
            }

        if decision.next_action == SupportDiagnosticAction.POLICY_BLOCKED:
            logger.info("support_diagnostic_policy_block_without_authoritative_policy")
            return None

        if decision.next_action == SupportDiagnosticAction.NOT_SUPPORTED:
            message_text = decision.user_message or render_message("support.not_sure", locale)
            return {
                "result": SupportResult(
                    outcome=SupportOutcome.OK,
                    response=message_text,
                    final_message=message_text,
                )
            }

        if decision.next_action == SupportDiagnosticAction.ASK_REFERENCE:
            return {
                "result": SupportResult(
                    outcome=SupportOutcome.NEEDS_INPUT,
                    response=decision.user_message or render_message("support.ask_reference", locale),
                )
            }

        if decision.next_action == SupportDiagnosticAction.ASK_CLARIFICATION:
            return {
                "result": SupportResult(
                    outcome=SupportOutcome.NEEDS_INPUT,
                    response=decision.user_message or render_message("support.ask_clarification", locale),
                )
            }

        effective_intent = (
            SupportIntent.RETRY_TRANSFER
            if decision.next_action == SupportDiagnosticAction.PREPARE_RETRY_HANDOFF
            else decision.intent
        )
        effective_ref = decision.transaction_ref or tx_ref or extraction.transaction_ref
        if decision.next_action in {
            SupportDiagnosticAction.LOOKUP_TRANSACTION,
            SupportDiagnosticAction.EXPLAIN_TRANSACTION,
            SupportDiagnosticAction.PREPARE_RETRY_HANDOFF,
        } and not self._diagnostic_has_transaction_grounding(
            transaction=resolved_tx,
            tx_ref=effective_ref,
            quoted_message_id=quoted_message_id,
        ):
            logger.info("support_diagnostic_missing_transaction_grounding")
            return None

        next_step_by_action = {
            SupportDiagnosticAction.LOOKUP_TRANSACTION: NextStep.LOOKUP_TRANSACTION,
            SupportDiagnosticAction.EXPLAIN_TRANSACTION: NextStep.LOOKUP_TRANSACTION,
            SupportDiagnosticAction.PREPARE_RETRY_HANDOFF: NextStep.LOOKUP_TRANSACTION,
            SupportDiagnosticAction.LOOKUP_TICKET: NextStep.LOOKUP_TICKET,
            SupportDiagnosticAction.CREATE_TICKET: NextStep.CREATE_TICKET,
            SupportDiagnosticAction.ESCALATE_TICKET: NextStep.CREATE_TICKET,
        }
        next_step = next_step_by_action.get(decision.next_action)
        if next_step is None:
            return None

        return {
            "next_step": next_step,
            "intent": effective_intent,
            "transaction_ref": effective_ref,
            "ticket_code": decision.ticket_code,
            "reason": decision.reason or "diagnostic_agent",
        }

    async def _handle_pending_reference_followup(
        self,
        *,
        user_id: str,
        support_ctx: Any,
        message: str,
        locale: str,
    ) -> tuple[dict[str, Any] | None, SupportResult | None]:
        pending = support_ctx.pending_reference
        if pending is None or not pending.candidates:
            return None, None

        if _ACK_ONLY_RE.match(message):
            reminder = pending.reminder or _build_reference_reminder(pending.candidates)
            await self.context_manager.save(user_id, support_ctx)
            return None, SupportResult(outcome=SupportOutcome.NEEDS_INPUT, response=reminder)

        matches = self._match_reference_candidates(message=message, candidates=pending.candidates)
        if len(matches) != 1:
            return None, None

        resolved = await self._load_transaction_dict(matches[0].transaction_id)
        if resolved is None:
            return None, None

        support_ctx.pending_reference = None
        support_ctx.last_transaction_ref = str(resolved.get("id") or resolved.get("transaction_id") or "")
        thread_state = support_ctx.receipt_thread_state
        if isinstance(thread_state, ReceiptBatchThreadState):
            updated_thread = self._build_receipt_thread_state(
                async_group_id=thread_state.async_group_id,
                candidates=thread_state.candidates,
                served_transaction_ids=thread_state.served_transaction_ids + [matches[0].transaction_id],
                last_selector_result_ids=[matches[0].transaction_id],
                last_served_transaction_ids=[matches[0].transaction_id],
            )
            support_ctx.receipt_thread_state = updated_thread
        await self.context_manager.save(user_id, support_ctx)
        if pending.intent:
            try:
                intent = SupportIntent(pending.intent)
            except ValueError:
                intent = None
            if intent is not None:
                if policy_message := capability_block_message(domain="support", action="retry_payout", locale=locale):
                    if intent == SupportIntent.RETRY_TRANSFER:
                        return None, SupportResult(
                            outcome=SupportOutcome.OK,
                            response=policy_message,
                            final_message=policy_message,
                        )
                response = await self._dispatch_handler(intent, resolved, user_id=user_id, locale=locale)
                return None, self._result_from_support_response(response, intent=intent, locale=locale)
        return resolved, None

    async def _load_recent_candidate_transaction(
        self,
        candidate: SupportReferenceCandidate,
    ) -> dict[str, Any] | None:
        resolved = await self._load_transaction_dict(candidate.transaction_id)
        if resolved is None:
            return None
        if candidate.error_message and not resolved.get("error_message"):
            resolved["error_message"] = candidate.error_message
        if candidate.failure_category and not resolved.get("failure_category"):
            resolved["failure_category"] = candidate.failure_category
        if candidate.final_status:
            resolved["status"] = candidate.final_status
        return resolved

    async def _resolve_recent_batch_support_reference(
        self,
        *,
        user_id: str,
        support_ctx: Any,
        context: dict[str, Any],
        message: str,
        locale: str,
        intent: SupportIntent,
    ) -> tuple[dict[str, Any] | None, SupportResult | None]:
        if intent not in {SupportIntent.FAILED_TRANSFER, SupportIntent.RETRY_TRANSFER}:
            return None, None
        identity = _support_identity(context)
        recent_batch = await get_recent_batch_reference(self.context_manager.redis, identity=identity)
        if recent_batch is None:
            return None, None
        candidates = [candidate for leg in recent_batch["legs"] if (candidate := _leg_to_candidate(leg)) is not None]
        failed_candidates = [candidate for candidate in candidates if candidate.final_status == "failed"]
        if not failed_candidates:
            return None, None

        matches = self._match_reference_candidates(message=message, candidates=failed_candidates)
        selected = matches if matches else failed_candidates if len(failed_candidates) == 1 else []
        if len(selected) == 1:
            resolved = await self._load_recent_candidate_transaction(selected[0])
            if resolved is None:
                return None, None
            support_ctx.last_transaction_ref = str(resolved.get("id") or resolved.get("transaction_id") or "")
            support_ctx.pending_reference = None
            await self.context_manager.save(user_id, support_ctx)
            return resolved, None

        await self._save_pending_reference(
            user_id=user_id,
            support_ctx=support_ctx,
            candidates=failed_candidates,
            locale=locale,
            intent=intent,
        )
        return None, SupportResult(
            outcome=SupportOutcome.NEEDS_INPUT,
            response=_build_reference_prompt(failed_candidates),
        )

    async def _resolve_recent_batch_receipt_reference(
        self,
        *,
        user_id: str,
        support_ctx: Any,
        context: dict[str, Any],
        message: str,
        locale: str,
    ) -> tuple[dict[str, Any] | None, SupportResult | None]:
        identity = _support_identity(context)
        recent_batch = await get_recent_batch_reference(self.context_manager.redis, identity=identity)
        thread_state = support_ctx.receipt_thread_state

        candidates: list[SupportReferenceCandidate] = []
        async_group_id: str | None = None
        if recent_batch is not None:
            async_group_id = str(recent_batch.get("async_group_id") or "")
            candidates = [candidate for leg in recent_batch["legs"] if (candidate := _leg_to_candidate(leg)) is not None]
            if (
                isinstance(thread_state, ReceiptBatchThreadState)
                and async_group_id
                and thread_state.async_group_id != async_group_id
            ):
                support_ctx.receipt_thread_state = None
                thread_state = None

        if not candidates and isinstance(thread_state, ReceiptBatchThreadState):
            candidates = list(thread_state.candidates)
            async_group_id = thread_state.async_group_id

        if not candidates:
            return None, None

        selected, selection, exhausted_message, prompt_candidates = self._select_recent_batch_candidates(
            message=message,
            candidates=candidates,
            thread_state=thread_state if isinstance(thread_state, ReceiptBatchThreadState) else None,
        )
        if exhausted_message is not None:
            if async_group_id:
                support_ctx.receipt_thread_state = self._build_receipt_thread_state(
                    async_group_id=async_group_id,
                    candidates=candidates,
                    served_transaction_ids=(
                        thread_state.served_transaction_ids
                        if isinstance(thread_state, ReceiptBatchThreadState)
                        else [candidate.transaction_id for candidate in self._eligible_receipt_candidates(candidates)]
                    ),
                    last_selector_result_ids=[],
                    last_served_transaction_ids=[],
                )
                await self.context_manager.save(user_id, support_ctx)
            return None, SupportResult(
                outcome=SupportOutcome.OK,
                response=exhausted_message,
                final_message=exhausted_message,
            )

        if selected:
            jobs, skipped_failed, skipped_processing, skipped_non_transfer = await self._build_receipt_jobs_for_candidates(
                selected=selected,
                candidates=candidates,
                context=context,
                locale=locale,
            )
            if not jobs:
                response = "I can't send receipts for that batch yet because none of those transfer legs completed successfully."
                return None, SupportResult(
                    outcome=SupportOutcome.OK,
                    response=response,
                    final_message=response,
                )

            await self._clear_pending_reference(user_id=user_id, support_ctx=support_ctx)
            selected_ids = [candidate.transaction_id for candidate in selected]
            served_ids = (
                list(thread_state.served_transaction_ids)
                if isinstance(thread_state, ReceiptBatchThreadState)
                else []
            )
            if async_group_id:
                updated_thread = self._build_receipt_thread_state(
                    async_group_id=async_group_id,
                    candidates=candidates,
                    served_transaction_ids=served_ids + selected_ids,
                    last_selector_result_ids=selected_ids,
                    last_served_transaction_ids=selected_ids,
                )
                await self._save_receipt_thread_state(
                    user_id=user_id,
                    support_ctx=support_ctx,
                    thread_state=updated_thread,
                )

            response = _build_batch_receipt_ack(
                total_jobs=len(jobs),
                skipped_failed=skipped_failed,
                skipped_processing=skipped_processing,
                skipped_non_transfer=skipped_non_transfer,
            )
            if len(selected) == 1 and selection and selection.selection_mode == "subset":
                resolved = await self._load_transaction_dict(selected[0].transaction_id)
                if resolved is not None:
                    support_ctx.last_transaction_ref = str(
                        resolved.get("id") or resolved.get("transaction_id") or ""
                    )
                    await self.context_manager.save(user_id, support_ctx)
                    return resolved, None
            return None, SupportResult(
                outcome=SupportOutcome.OK,
                response=response,
                final_message=response,
                receipt_jobs=jobs,
            )

        matches = self._match_reference_candidates(message=message, candidates=candidates)
        if len(matches) > 1:
            prompt_candidates = sorted(matches, key=lambda candidate: candidate.ordinal)
        elif prompt_candidates is None:
            prompt_candidates = sorted(candidates, key=lambda candidate: candidate.ordinal)

        await self._save_pending_reference(
            user_id=user_id,
            support_ctx=support_ctx,
            candidates=prompt_candidates,
            locale=locale,
        )
        if async_group_id:
            existing_served = (
                list(thread_state.served_transaction_ids)
                if isinstance(thread_state, ReceiptBatchThreadState)
                else []
            )
            await self._save_receipt_thread_state(
                user_id=user_id,
                support_ctx=support_ctx,
                thread_state=self._build_receipt_thread_state(
                    async_group_id=async_group_id,
                    candidates=candidates,
                    served_transaction_ids=existing_served,
                    last_selector_result_ids=[],
                    last_served_transaction_ids=[],
                ),
            )
        return None, SupportResult(
            outcome=SupportOutcome.NEEDS_INPUT,
            response=_build_reference_prompt(prompt_candidates),
        )

    async def run(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> SupportResult:
        """Run the Support flow."""
        del pin_verified
        phone_number = context.get("phone_number", "")
        user_id = context.get("user_id") or phone_number
        locale = LocaleManager.normalize(context.get("language")).value
        message = (user_message or "").strip()
        if policy_message := capability_block_message(domain="support", action="collect_details", locale=locale):
            logger.info("support_worker_capability_blocked")
            return SupportResult(
                outcome=SupportOutcome.OK,
                response=policy_message,
                final_message=policy_message,
            )

        # Extract inputs from payload
        quoted_message_id = payload.get("quoted_message_id")
        transaction = payload.get("transaction")

        support_ctx = await self.context_manager.get(user_id)
        pending_tx, pending_response = await self._handle_pending_reference_followup(
            user_id=user_id,
            support_ctx=support_ctx,
            message=message,
            locale=locale,
        )
        if pending_response is not None:
            return pending_response
        if pending_tx is not None:
            return self._build_receipt_result(
                transaction=pending_tx,
                context=context,
                locale=locale,
            )

        try:
            # 1. Classification
            intent_str = payload.get("intent")
            intent = None
            classification = None

            if intent_str:
                try:
                    intent = SupportIntent(intent_str)
                except ValueError:
                    logger.info("support_intent_invalid", provided=intent_str)

            if not intent:
                result = await self.classifier.classify(message)
                classification = result
                if self.classifier.is_support_intent(result):
                    intent = result.intent

            contextual_intent, contextual_ref = self._contextual_followup_reference(
                support_ctx=support_ctx,
                message=message,
            )
            if contextual_intent is not None:
                intent = intent or contextual_intent

            if not intent:
                return SupportResult(
                    outcome=SupportOutcome.OK,
                    response=render_message("support.not_sure", locale),
                )

            # 2. Extract Transaction Reference
            tx_ref = None
            if classification and classification.transaction_ref:
                tx_ref = TransactionReference(
                    transaction_id=classification.transaction_ref.transaction_id,
                    amount=classification.transaction_ref.amount,
                    recipient_name=classification.transaction_ref.recipient_name,
                    date_hint=classification.transaction_ref.date_hint,
                    use_quoted=classification.transaction_ref.use_quoted,
                    use_recent=classification.transaction_ref.use_recent,
                )
            if quoted_message_id:
                tx_ref = tx_ref or TransactionReference()
                tx_ref.use_quoted = True
            if contextual_ref is not None:
                tx_ref = contextual_ref
            if self._should_verify_latest_transaction_status(intent, message):
                tx_ref = tx_ref or TransactionReference()
                tx_ref.use_recent = True

            extraction = SupportExtractionResult(
                intent=intent,
                intent_confidence=classification.confidence if classification else 1.0,
                transaction_ref=tx_ref or TransactionReference(),
                raw_issue=message,
            )
            has_explicit_tx_ref = bool(tx_ref and self.resolver._has_explicit_ref(tx_ref))  # type: ignore[attr-defined]

            resolved_tx = transaction
            if (
                resolved_tx is None
                and intent == SupportIntent.RECEIPT_REQUEST
                and not quoted_message_id
                and not has_explicit_tx_ref
            ):
                resolved_tx, receipt_followup = await self._resolve_recent_batch_receipt_reference(
                    user_id=user_id,
                    support_ctx=support_ctx,
                    context=context,
                    message=message,
                    locale=locale,
                )
                if receipt_followup is not None:
                    return receipt_followup

            if (
                resolved_tx is None
                and intent in {SupportIntent.FAILED_TRANSFER, SupportIntent.RETRY_TRANSFER}
                and not quoted_message_id
                and not has_explicit_tx_ref
            ):
                resolved_tx, recent_support_followup = await self._resolve_recent_batch_support_reference(
                    user_id=user_id,
                    support_ctx=support_ctx,
                    context=context,
                    message=message,
                    locale=locale,
                    intent=intent,
                )
                if recent_support_followup is not None:
                    return recent_support_followup
                if resolved_tx is not None:
                    tx_ref = tx_ref or TransactionReference()
                    tx_ref.use_recent = True
                    extraction.transaction_ref.use_recent = True

            if resolved_tx is not None and intent == SupportIntent.RECEIPT_REQUEST:
                return self._build_receipt_result(
                    transaction=resolved_tx,
                    context=context,
                    locale=locale,
                )

            decision = None
            diagnostic_ticket_code = None
            diagnostic_reason = None
            diagnostic_route = await self._diagnostic_route(
                support_ctx=support_ctx,
                context=context,
                message=message,
                locale=locale,
                intent=intent,
                classification=classification,
                extraction=extraction,
                tx_ref=tx_ref,
                resolved_tx=resolved_tx if isinstance(resolved_tx, dict) else None,
                quoted_message_id=quoted_message_id,
            )
            if diagnostic_route is not None and diagnostic_route.get("result") is not None:
                return diagnostic_route["result"]

            if diagnostic_route is not None:
                intent = diagnostic_route["intent"]
                tx_ref = diagnostic_route.get("transaction_ref") or tx_ref
                extraction.intent = intent
                if tx_ref is not None:
                    extraction.transaction_ref = tx_ref
                support_ctx.last_issue_intent = intent
                await self.context_manager.save(user_id, support_ctx)
                next_step = diagnostic_route["next_step"]
                diagnostic_ticket_code = diagnostic_route.get("ticket_code")
                diagnostic_reason = diagnostic_route.get("reason")
            else:
                # 3. Micro-Resolution (Context Aware)
                decision = micro_resolve(
                    extraction=extraction,
                    context=support_ctx,
                    has_quoted_message=bool(quoted_message_id),
                    language=locale,
                )
                await self.context_manager.save(user_id, decision.context)
                next_step = decision.next_step

            # 4. Handle Routing
            if next_step == NextStep.ASK_REFERENCE:
                return SupportResult(
                    outcome=SupportOutcome.NEEDS_INPUT,
                    response=render_message("support.ask_reference", locale),
                )
            elif next_step == NextStep.ASK_CLARIFICATION:
                await self.context_manager.increment_attempts(user_id)
                prompt = render_message("support.ask_clarification", locale)
                if decision.prompts:
                    if decision.prompts[0].key == "support.negotiate":
                        prompt = decision.negotiation.message if decision.negotiation else prompt
                return SupportResult(
                    outcome=SupportOutcome.NEEDS_INPUT,
                    response=prompt,
                )

            if next_step == NextStep.LOOKUP_TICKET:
                response = await self._dispatch_handler(
                    intent,
                    resolved_tx,
                    user_id=user_id,
                    locale=locale,
                    ticket_code=diagnostic_ticket_code or _ticket_code_from_message(message),
                )
                return self._result_from_support_response(response, intent=intent, locale=locale)

            # 5. Transaction Resolution
            if not resolved_tx and next_step in (
                NextStep.LOOKUP_TRANSACTION,
                NextStep.EXPLAIN_STATUS,
            ):
                tx_obj, method = await self.resolver.resolve(
                    user_id,
                    tx_ref,
                    quoted_message_id,
                    recent_status_priority=self._recent_status_priority(intent),
                    prefer_latest_recent=(
                        self._should_verify_latest_transaction_status(intent, message)
                        or self._is_recent_transaction_reference(message)
                    ),
                )
                if tx_obj:
                    resolved_tx = self.resolver.transaction_to_dict(tx_obj)

                if not tx_obj and method == "not_found":
                    updated_context = await self.context_manager.increment_attempts(user_id)
                    # Start linear escalation after max attempts -> handled next time or via escalation logic
                    if updated_context.attempts >= SUPPORT_LIMITS["max_escalation_attempts"]:
                        return await self._create_ticket_response(
                            user_id,
                            intent,
                            None,
                            "tx_not_found_max_attempts",
                            locale=locale,
                        )

                    return SupportResult(
                        outcome=SupportOutcome.OK,
                        response=render_message("support.tx_not_found", locale),
                    )

                if not tx_obj and method == "ambiguous":
                    return SupportResult(
                        outcome=SupportOutcome.NEEDS_INPUT,
                        response=render_message("support.tx_ambiguous", locale),
                    )

            # 6. Dispatch to Handler
            if next_step == NextStep.CREATE_TICKET:
                reason = diagnostic_reason or (
                    decision.escalation.reason if decision and decision.escalation else "micro_resolver_escalation"
                )
                return await self._create_ticket_response(user_id, intent, resolved_tx, reason, locale=locale)

            if resolved_tx or intent in (SupportIntent.TICKET_STATUS, SupportIntent.FRAUD_REPORT):
                if intent == SupportIntent.RECEIPT_REQUEST and isinstance(resolved_tx, dict):
                    return self._build_receipt_result(
                        transaction=resolved_tx,
                        context=context,
                        locale=locale,
                    )
                response = await self._dispatch_handler(intent, resolved_tx, user_id=user_id, locale=locale)

                if response and response.next_step == "NEEDS_INFO":
                    await self.context_manager.increment_attempts(user_id)

                if isinstance(resolved_tx, dict):
                    support_ctx.pending_reference = None
                    support_ctx.last_transaction_ref = str(
                        resolved_tx.get("id") or resolved_tx.get("transaction_id") or support_ctx.last_transaction_ref or ""
                    )
                    await self.context_manager.save(user_id, support_ctx)

                return self._result_from_support_response(response, intent=intent, locale=locale)

            return SupportResult(
                outcome=SupportOutcome.OK,
                response=render_message("support.need_tx_reference", locale),
            )

        except Exception as e:
            logger.error(f"Support worker failed: {e}", exc_info=True)
            return SupportResult(outcome=SupportOutcome.FAILED, error=str(e))

    async def _create_ticket_response(
        self,
        user_id: str,
        intent: Any,
        transaction: Any,
        reason: str,
        *,
        locale: str = "en",
    ) -> SupportResult:
        if policy_message := capability_block_message(domain="support", action="create_ticket", locale=locale):
            return SupportResult(
                outcome=SupportOutcome.OK,
                response=policy_message,
                final_message=policy_message,
            )

        if not self._ticket_service:
            return SupportResult(
                outcome=SupportOutcome.OK,
                response=render_message("support.escalation_unavailable", locale),
            )

        resp = await handle_escalation(
            user_id=user_id,
            intent=intent.value if hasattr(intent, "value") else str(intent),
            ticket_service=self._ticket_service,
            transaction=transaction,
            reason=reason,
            locale=locale,
        )

        ticket_code = None
        if resp.escalation and resp.escalation.context:
            ticket_code = resp.escalation.context.get("ticket_code")

        await self.context_manager.reset_on_resolution(
            user_id=user_id,
            ticket_id=ticket_code,
            transaction_ref=transaction.get("transaction_id") if transaction else None,
        )

        return SupportResult(
            outcome=SupportOutcome.OK,
            response=resp.message,
            final_message=resp.message,
            ticket_code=ticket_code,
            escalation=resp.escalation,
        )

    async def _dispatch_handler(
        self,
        intent: Any,
        transaction: Any,
        *,
        user_id: str,
        locale: str,
        ticket_code: str | None = None,
    ) -> SupportResponse:
        """Dispatch to handlers."""
        # Reuse the logic from SupportFlowGraph._dispatch_handler
        # Map intents to handler functions
        if intent == SupportIntent.TICKET_STATUS:
            # Ticket status needs context
            ctx = await self.context_manager.get(user_id)
            if not self._ticket_service:
                return SupportResponse(message=render_message("support.unavailable", locale))
            return await handle_ticket_status(
                user_id=user_id,
                ticket_service=self._ticket_service,
                ticket_code=ticket_code,
                last_ticket_id=ctx.last_ticket_id,
                locale=locale,
            )

        if intent == SupportIntent.TRANSFER_STATUS:
            return await handle_transfer_status(transaction, locale=locale)
        elif intent == SupportIntent.PENDING_TRANSFER:
            return await handle_pending(transaction, locale=locale)
        elif intent == SupportIntent.FAILED_TRANSFER:
            return await handle_failure_reason(transaction, locale=locale)
        elif intent == SupportIntent.WRONG_DEBIT:
            return await handle_wrong_debit(transaction, locale=locale)
        elif intent == SupportIntent.REVERSAL_REFUND or intent == SupportIntent.WRONG_RECIPIENT:
            return await handle_reversal_status(transaction, locale=locale)
        elif intent == SupportIntent.RETRY_TRANSFER:
            return await handle_retry(transaction, locale=locale)
        elif intent == SupportIntent.FRAUD_REPORT:
            return await handle_fraud(transaction, locale=locale)
        elif intent == SupportIntent.RECEIPT_REQUEST:
            return await handle_receipt_request(transaction, locale=locale)
        elif intent == SupportIntent.GENERAL_TX_ISSUE:
            return await handle_transfer_status(transaction, locale=locale)
        else:
            return SupportResponse(message=render_message("support.not_sure", locale))
