"""Recent-batch reference and receipt follow-up flow for support."""

import re
from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import SupportOutcome, SupportResult
from apps.chat.src.agent.workers.support.diagnostic_routing import support_identity
from apps.chat.src.agent.workers.support.handler_dispatch import SupportHandlerDispatcher
from apps.chat.src.agent.workers.support.models import (
    ReceiptBatchThreadState,
    SupportIntent,
    SupportReferenceCandidate,
)
from apps.chat.src.agent.workers.support.reference_selection import (
    build_batch_receipt_ack,
    build_receipt_thread_state,
    build_reference_prompt,
    build_reference_reminder,
    eligible_receipt_candidates,
    leg_to_candidate,
    match_reference_candidates,
    pending_reference_state,
    select_recent_batch_candidates,
)
from apps.chat.src.agent.workers.support.results import build_receipt_job, result_from_support_response
from banking.policy.service import capability_block_message
from banking.presentation.i18n.renderer import render_message
from banking.transactions.runtime.async_group_recent_batch import get_recent_batch_reference

_ACK_ONLY_RE = re.compile(r"^(ok(?:ay)?|alright|yes|yeah|yep|sure)\.?$", re.IGNORECASE)


class SupportReferenceFlow:
    """Resolves support follow-ups that point at recent batch transactions."""

    def __init__(
        self,
        *,
        context_manager: Any,
        resolver: Any,
        handler_dispatcher: SupportHandlerDispatcher,
    ) -> None:
        self._context_manager = context_manager
        self._resolver = resolver
        self._handler_dispatcher = handler_dispatcher

    async def _load_transaction_dict(self, transaction_id: str) -> dict[str, Any] | None:
        tx = await self._resolver.tx_repo.get_by_id(transaction_id)
        if not tx:
            return None
        return self._resolver.transaction_to_dict(tx)

    async def _save_receipt_thread_state(
        self,
        *,
        user_id: str,
        support_ctx: Any,
        thread_state: ReceiptBatchThreadState,
    ) -> None:
        support_ctx.receipt_thread_state = thread_state
        await self._context_manager.save(user_id, support_ctx)

    async def _save_pending_reference(
        self,
        *,
        user_id: str,
        support_ctx: Any,
        candidates: list[SupportReferenceCandidate],
        locale: str,
        intent: SupportIntent | None = None,
    ) -> None:
        support_ctx.pending_reference = pending_reference_state(candidates=candidates, locale=locale, intent=intent)
        await self._context_manager.save(user_id, support_ctx)

    async def _clear_pending_reference(self, *, user_id: str, support_ctx: Any) -> None:
        if support_ctx.pending_reference is None:
            return
        support_ctx.pending_reference = None
        await self._context_manager.save(user_id, support_ctx)

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
            jobs.append(build_receipt_job(transaction=transaction, context=context, locale=locale))
        return jobs, skipped_failed, skipped_processing, skipped_non_transfer

    async def handle_pending_reference_followup(
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
            reminder = pending.reminder or build_reference_reminder(pending.candidates, locale)
            await self._context_manager.save(user_id, support_ctx)
            return None, SupportResult(outcome=SupportOutcome.NEEDS_INPUT, response=reminder)

        matches = match_reference_candidates(message=message, candidates=pending.candidates)
        if len(matches) != 1:
            return None, None

        resolved = await self._load_transaction_dict(matches[0].transaction_id)
        if resolved is None:
            return None, None

        support_ctx.pending_reference = None
        support_ctx.last_transaction_ref = str(resolved.get("id") or resolved.get("transaction_id") or "")
        thread_state = support_ctx.receipt_thread_state
        if isinstance(thread_state, ReceiptBatchThreadState):
            support_ctx.receipt_thread_state = build_receipt_thread_state(
                async_group_id=thread_state.async_group_id,
                candidates=thread_state.candidates,
                locale=locale,
                served_transaction_ids=thread_state.served_transaction_ids + [matches[0].transaction_id],
                last_selector_result_ids=[matches[0].transaction_id],
                last_served_transaction_ids=[matches[0].transaction_id],
            )
        await self._context_manager.save(user_id, support_ctx)
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
                response = await self._handler_dispatcher.dispatch(intent, resolved, user_id=user_id, locale=locale)
                return None, result_from_support_response(response, intent=intent, locale=locale)
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

    async def resolve_recent_batch_support_reference(
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
        identity = support_identity(context)
        recent_batch = await get_recent_batch_reference(self._context_manager.redis, identity=identity)
        if recent_batch is None:
            return None, None
        candidates = [candidate for leg in recent_batch["legs"] if (candidate := leg_to_candidate(leg)) is not None]
        failed_candidates = [candidate for candidate in candidates if candidate.final_status == "failed"]
        if not failed_candidates:
            return None, None

        matches = match_reference_candidates(message=message, candidates=failed_candidates)
        selected = matches if matches else failed_candidates if len(failed_candidates) == 1 else []
        if len(selected) == 1:
            resolved = await self._load_recent_candidate_transaction(selected[0])
            if resolved is None:
                return None, None
            support_ctx.last_transaction_ref = str(resolved.get("id") or resolved.get("transaction_id") or "")
            support_ctx.pending_reference = None
            await self._context_manager.save(user_id, support_ctx)
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
            response=build_reference_prompt(failed_candidates, locale),
        )

    async def resolve_recent_batch_receipt_reference(
        self,
        *,
        user_id: str,
        support_ctx: Any,
        context: dict[str, Any],
        message: str,
        locale: str,
    ) -> tuple[dict[str, Any] | None, SupportResult | None]:
        identity = support_identity(context)
        recent_batch = await get_recent_batch_reference(self._context_manager.redis, identity=identity)
        thread_state = support_ctx.receipt_thread_state

        candidates: list[SupportReferenceCandidate] = []
        async_group_id: str | None = None
        if recent_batch is not None:
            async_group_id = str(recent_batch.get("async_group_id") or "")
            candidates = [candidate for leg in recent_batch["legs"] if (candidate := leg_to_candidate(leg)) is not None]
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

        selected, selection, exhausted_message, prompt_candidates = select_recent_batch_candidates(
            message=message,
            candidates=candidates,
            thread_state=thread_state if isinstance(thread_state, ReceiptBatchThreadState) else None,
            locale=locale,
        )
        if exhausted_message is not None:
            if async_group_id:
                support_ctx.receipt_thread_state = build_receipt_thread_state(
                    async_group_id=async_group_id,
                    candidates=candidates,
                    locale=locale,
                    served_transaction_ids=(
                        thread_state.served_transaction_ids
                        if isinstance(thread_state, ReceiptBatchThreadState)
                        else [candidate.transaction_id for candidate in eligible_receipt_candidates(candidates)]
                    ),
                    last_selector_result_ids=[],
                    last_served_transaction_ids=[],
                )
                await self._context_manager.save(user_id, support_ctx)
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
                response = render_message("support.receipt.batch_no_completed", locale)
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
                await self._save_receipt_thread_state(
                    user_id=user_id,
                    support_ctx=support_ctx,
                    thread_state=build_receipt_thread_state(
                        async_group_id=async_group_id,
                        candidates=candidates,
                        locale=locale,
                        served_transaction_ids=served_ids + selected_ids,
                        last_selector_result_ids=selected_ids,
                        last_served_transaction_ids=selected_ids,
                    ),
                )

            response = build_batch_receipt_ack(
                total_jobs=len(jobs),
                skipped_failed=skipped_failed,
                skipped_processing=skipped_processing,
                skipped_non_transfer=skipped_non_transfer,
                locale=locale,
            )
            if len(selected) == 1 and selection and selection.selection_mode == "subset":
                resolved = await self._load_transaction_dict(selected[0].transaction_id)
                if resolved is not None:
                    support_ctx.last_transaction_ref = str(
                        resolved.get("id") or resolved.get("transaction_id") or ""
                    )
                    await self._context_manager.save(user_id, support_ctx)
                    return resolved, None
            return None, SupportResult(
                outcome=SupportOutcome.OK,
                response=response,
                final_message=response,
                receipt_jobs=jobs,
            )

        matches = match_reference_candidates(message=message, candidates=candidates)
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
                thread_state=build_receipt_thread_state(
                    async_group_id=async_group_id,
                    candidates=candidates,
                    locale=locale,
                    served_transaction_ids=existing_served,
                    last_selector_result_ids=[],
                    last_served_transaction_ids=[],
                ),
            )
        return None, SupportResult(
            outcome=SupportOutcome.NEEDS_INPUT,
            response=build_reference_prompt(prompt_candidates, locale),
        )


__all__ = ["SupportReferenceFlow"]
