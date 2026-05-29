"""Diagnostic-agent routing for support conversations."""

from __future__ import annotations

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import SupportOutcome, SupportResult
from apps.chat.src.agent.workers.support.capabilities import SupportAction
from apps.chat.src.agent.workers.support.diagnostic_agent import (
    SupportDiagnosticAgent,
    build_support_diagnostic_context,
)
from apps.chat.src.agent.workers.support.micro_resolver import NextStep
from apps.chat.src.agent.workers.support.models import (
    SupportDiagnosticAction,
    SupportDiagnosticDecision,
    SupportExtractionResult,
    SupportIntent,
    SupportReferenceCandidate,
    TransactionReference,
)
from apps.chat.src.agent.workers.support.reference_selection import leg_to_candidate
from banking.transactions.runtime.async_group_recent_batch import get_recent_batch_reference
from shared.config.settings import settings
from shared.i18n.renderer import render_message
from shared.policy.adapters import is_capability_supported
from shared.policy.service import capability_block_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def support_identity(context: dict[str, Any]) -> str | None:
    for key in ("channel_identity", "phone_number", "user_id"):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


class SupportDiagnosticRouter:
    """Runs the optional diagnostic agent and converts decisions into worker routes."""

    def __init__(self, *, llm: Any, resolver: Any, context_manager: Any) -> None:
        self._llm = llm
        self._resolver = resolver
        self._context_manager = context_manager
        self._agent: SupportDiagnosticAgent | None = None

    def _get_agent(self) -> SupportDiagnosticAgent | None:
        if self._agent is not None:
            return self._agent
        try:
            self._agent = SupportDiagnosticAgent(self._llm)
        except Exception as exc:
            logger.info("support_diagnostic_agent_unavailable", error=str(exc))
            return None
        return self._agent

    @staticmethod
    def _support_capability_summary() -> dict[str, bool]:
        return {
            action.value: is_capability_supported(domain="support", action=action.value)
            for action in SupportAction
        }

    async def _recent_candidates(self, context: dict[str, Any]) -> list[SupportReferenceCandidate]:
        identity = support_identity(context)
        recent_batch = await get_recent_batch_reference(self._context_manager.redis, identity=identity)
        if recent_batch is None:
            return []
        candidates = [candidate for leg in recent_batch["legs"] if (candidate := leg_to_candidate(leg)) is not None]
        return [candidate for candidate in candidates if candidate.final_status in {"failed", "processing"}]

    @staticmethod
    def _required_actions(decision: SupportDiagnosticDecision) -> list[str]:
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

    def _has_transaction_grounding(
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
        return self._resolver._has_explicit_ref(tx_ref)  # type: ignore[attr-defined]

    async def route(
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
        ticket_code: str | None,
    ) -> dict[str, Any] | None:
        if not settings.enable_support_diagnostic_agent:
            return None
        agent = self._get_agent()
        if agent is None:
            return None

        diagnostic_context = build_support_diagnostic_context(
            message=message,
            locale=locale,
            support_context=support_ctx,
            intent=intent,
            classification=classification,
            transaction=resolved_tx,
            recent_candidates=await self._recent_candidates(context),
            ticket_code=ticket_code,
            latest_ticket_id=getattr(support_ctx, "last_ticket_id", None),
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

        required_actions = self._required_actions(decision)
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
        } and not self._has_transaction_grounding(
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
