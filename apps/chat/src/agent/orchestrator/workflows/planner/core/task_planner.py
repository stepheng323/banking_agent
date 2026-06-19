"""Task planner for breaking down user requests into executable tasks."""

import re
import time
from decimal import Decimal

from langchain_openai import ChatOpenAI

from apps.chat.src.agent.orchestrator.task_state.service import TaskStateService
from apps.chat.src.agent.orchestrator.workflows.planner.core import (
    task_planner_context_frame_prompts as context_frame_prompts,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core import (
    task_planner_interrupt_prompts as interrupt_prompts,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core import (
    task_planner_prompt_models as prompt_models,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core import (
    task_planner_quoted_replay_prompts as quoted_replay_prompts,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_model_wiring import (
    build_task_planner_structured_outputs,
    with_structured_output,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_normalizer import (
    normalize_planner_transaction_output_with_quality,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_normalizer_parsing import (
    extract_bank_candidates,
    parse_amount_value,
    single_unambiguous,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_observability import (
    invoke_structured_prompt,
    log_latency_span,
    model_name,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_prompt_runtime import (
    PLANNER_PROMPT_BASELINE_RESULT,
    build_runtime_planner_system_prompt,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_quality import PlannerPlanResult
from banking.transactions.shared.confirmation.classifier import classify_confirmation_reply
from banking.transactions.shared.confirmation.models import (
    ConfirmationDecision,
    ConfirmationPromptKind,
)
from shared.observability.llm import build_llm_runnable_config
from shared.observability.llm_call_metrics import record_llm_call, structured_output_metrics
from shared.types.planner import (
    BatchSlotPatchDecision,
    ContextFrameFollowupDecision,
    ContextFrameReplayModifier,
    InterruptRouteDecision,
    PendingActionEditDecision,
    PlannerOutput,
    planner_output_model_for_transaction_executors,
)
from shared.types.quoted_replay import QuotedReplayInterpretation
from shared.utils.logging import get_logger

logger = get_logger(__name__)


PLANNER_USER_PROMPT_TEMPLATE = """User phone: {phone_number}
Context: {context}
Message: \"\"\"{user_message}\"\"\"
"""

_BATCH_CUE_RE = re.compile(r"\b(?:each|split|between|btw)\b", re.IGNORECASE)
_AMOUNT_TOKEN_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:k|m)?\b", re.IGNORECASE)
_RECIPIENT_SEGMENT_BOUNDARY_RE = re.compile(r"\b(?:then|from|using|with|via|through|while)\b", re.IGNORECASE)
_SOURCE_FIRST_TRANSFER_RE = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|please|pls|abeg|oya|jowo|biko|kindly)\s+)*"
    r"(?:use|using|from|with)\s+(?:my\s+)?(?P<source>.+?)\s+"
    r"(?:to\s+)?(?:send|transfer|pay|remit)\b(?P<tail>.+)$",
    re.IGNORECASE,
)
_TRANSFER_RECIPIENT_AFTER_AMOUNT_RE = re.compile(
    r"^\s*(?:to|si|ga|zuwa)\s+(?P<recipient>.+)$",
    re.IGNORECASE,
)
_NARRATION_TAIL_RE = re.compile(r"\s+\bfor\b\s+(?P<narration>.+)$", re.IGNORECASE)
_TRANSACTION_EXECUTOR_VALUES = {"transfer", "airtime", "data"}


def _format_hint_amount(amount: Decimal) -> str:
    formatted = format(amount, "f")
    return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted


def _extract_batch_recipients_exact(text: str) -> list[str]:
    if not _BATCH_CUE_RE.search(text):
        return []

    match = re.search(r"\b(?:between|btw)\b\s+(.+)", text, re.IGNORECASE)
    if match is None:
        match = re.search(r"\b(?:to|for|si|ga|zuwa)\b\s+(.+)", text, re.IGNORECASE)
    if match is None:
        return []

    segment = _RECIPIENT_SEGMENT_BOUNDARY_RE.split(match.group(1), maxsplit=1)[0].strip()
    segment = re.sub(r"\band\s+to\b", " and ", segment, flags=re.IGNORECASE)
    recipients: list[str] = []
    seen: set[str] = set()
    for raw_part in re.split(r"\s*,\s*|\s+\band\b\s+", segment, flags=re.IGNORECASE):
        part = re.sub(r"^(?:to|for|si|ga|zuwa)\s+", "", raw_part, flags=re.IGNORECASE).strip(" \t\r\n,.;:!?")
        if not part or _AMOUNT_TOKEN_RE.search(part):
            continue
        key = re.sub(r"[^a-z0-9]+", " ", part.lower()).strip()
        if key and key not in seen:
            seen.add(key)
            recipients.append(part)
    return recipients


def _build_clean_transfer_context_hint(text: str, prompt_signals: prompt_models.PlannerPromptSignals) -> str | None:
    transfer_expected = (
        "transfer" in prompt_signals.expected_transaction_executors
        or prompt_signals.forced_domain_owner == "transfer"
        or prompt_signals.active_flow_type == "transfer"
    )
    if not transfer_expected:
        return None

    recipients = _extract_batch_recipients_exact(text)
    if len(recipients) < 2:
        return None

    amount_match = _AMOUNT_TOKEN_RE.search(text)
    if amount_match is None:
        return None
    parsed_amount = parse_amount_value(amount_match.group(0))
    if parsed_amount is None or parsed_amount <= 0:
        return None

    if re.search(r"\beach\b", text, re.IGNORECASE):
        per_recipient_amount = parsed_amount
    else:
        per_recipient_amount = parsed_amount / Decimal(len(recipients))

    allocations = ",".join(
        f'{{recipient_name:"{recipient}",amount:{_format_hint_amount(per_recipient_amount)}}}'
        for recipient in recipients
    )
    return (
        "CLEAN_EXTRACTION_HINT: output exactly one transfer send_money task with "
        f"amount={_format_hint_amount(per_recipient_amount)}, recipient_allocations=[{allocations}]. "
        "Omit recipient_name and bank_name at task parameters level. "
        "Preserve aliases exactly; do not turn alias words into bank_name."
    )


def _transfer_expected(prompt_signals: prompt_models.PlannerPromptSignals) -> bool:
    return (
        "transfer" in prompt_signals.expected_transaction_executors
        or prompt_signals.forced_domain_owner == "transfer"
        or prompt_signals.active_flow_type == "transfer"
    )


def _clean_hint_text(value: str) -> str:
    return value.strip(" \t\r\n,.;:!?")


def _format_hint_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _format_hint_narration(value: str) -> str:
    cleaned = _clean_hint_text(value)
    if not cleaned:
        return ""
    return cleaned[:1].upper() + cleaned[1:]


def _build_clean_source_transfer_context_hint(
    text: str,
    prompt_signals: prompt_models.PlannerPromptSignals,
) -> str | None:
    if not _transfer_expected(prompt_signals):
        return None

    match = _SOURCE_FIRST_TRANSFER_RE.match(text)
    if match is None:
        return None

    source_bank, source_bank_ambiguous = single_unambiguous(extract_bank_candidates(match.group("source")))
    if source_bank_ambiguous or not isinstance(source_bank, str):
        return None

    tail = match.group("tail")
    amount_match = _AMOUNT_TOKEN_RE.search(tail)
    if amount_match is None:
        return None
    amount = parse_amount_value(amount_match.group(0))
    if amount is None or amount <= 0:
        return None

    recipient_match = _TRANSFER_RECIPIENT_AFTER_AMOUNT_RE.match(tail[amount_match.end() :])
    if recipient_match is None:
        return None

    recipient_text = recipient_match.group("recipient")
    narration: str | None = None
    narration_match = _NARRATION_TAIL_RE.search(recipient_text)
    if narration_match is not None:
        narration = _format_hint_narration(narration_match.group("narration"))
        recipient_text = recipient_text[: narration_match.start()]

    recipient_name = _clean_hint_text(recipient_text)
    if not recipient_name:
        return None

    fields = [
        f'"amount":{_format_hint_amount(amount)}',
        f'"source_bank_name":"{_format_hint_text(source_bank)}"',
        f'"recipient_name":"{_format_hint_text(recipient_name)}"',
    ]
    if narration:
        fields.append(f'"narration":"{_format_hint_text(narration)}"')

    return (
        "CLEAN_SOURCE_TRANSFER_HINT_JSON: output exactly one transfer send_money task with "
        f"parameters={{{','.join(fields)}}}. Copy these slots exactly; omit recipient_bank_name and bank_name. "
        f'Treat every word in "{_format_hint_text(recipient_name)}" as recipient alias text, not bank_name. '
        'Wrong: {"bank_name":"Access Bank"}.'
    )


def _augment_context_with_clean_transfer_hint(
    context: str,
    text: str,
    prompt_signals: prompt_models.PlannerPromptSignals,
) -> str:
    hints = [
        hint
        for hint in (
            _build_clean_transfer_context_hint(text, prompt_signals),
            _build_clean_source_transfer_context_hint(text, prompt_signals),
        )
        if hint is not None
    ]
    if not hints:
        return context
    hint_text = "\n".join(hints)
    if not context or context == "None":
        return hint_text
    return f"{context}\n{hint_text}"


def _planner_response_model_for_prompt(
    prompt_signals: prompt_models.PlannerPromptSignals,
    prompt_result: prompt_models.PlannerPromptBuildResult,
) -> type[PlannerOutput]:
    bundles = set(prompt_result.selected_bundle_ids)
    if "transfer_only" in bundles:
        return planner_output_model_for_transaction_executors(("transfer",))
    if "mixed_tx" in bundles:
        return planner_output_model_for_transaction_executors(prompt_signals.expected_transaction_executors)
    if "money_move" in bundles:
        if prompt_signals.expected_transaction_executors:
            return planner_output_model_for_transaction_executors(prompt_signals.expected_transaction_executors)
        if prompt_signals.active_flow_type in _TRANSACTION_EXECUTOR_VALUES:
            return planner_output_model_for_transaction_executors((prompt_signals.active_flow_type,))
        if prompt_signals.forced_domain_owner == "transfer":
            return planner_output_model_for_transaction_executors(("transfer",))
        return planner_output_model_for_transaction_executors(_TRANSACTION_EXECUTOR_VALUES)
    return PlannerOutput


def _planner_prompt_cache_key(response_type: type[PlannerOutput]) -> str:
    response_type_name = response_type.__name__
    suffix_by_response_type = {
        "PlannerOutputTransferOnly": "transfer_only",
        "PlannerOutputAirtimeOnly": "airtime_only",
        "PlannerOutputDataOnly": "data_only",
        "PlannerOutputTransferAirtime": "transfer_airtime",
        "PlannerOutputTransferData": "transfer_data",
        "PlannerOutputAirtimeData": "airtime_data",
        "PlannerOutputTransactionsOnly": "transactions_only",
    }
    return f"planner:{suffix_by_response_type.get(response_type_name, 'full')}"


class TaskPlanner:
    """Handles task planning for multi-step requests."""

    def __init__(
        self,
        planner_llm: ChatOpenAI,
        semantic_router_llm: ChatOpenAI | None = None,
        interrupt_llm: ChatOpenAI | None = None,
        task_state_service: TaskStateService | None = None,
    ) -> None:
        self.planner_llm = planner_llm
        self.semantic_router_llm = semantic_router_llm or interrupt_llm or planner_llm
        self.interrupt_llm = interrupt_llm or planner_llm
        self.uses_dedicated_interrupt_model = interrupt_llm is not None
        self.uses_dedicated_semantic_router_model = semantic_router_llm is not None
        # PlannerOutput now includes clause-local free-form extracted fields. That shape is valid for
        # tool/function calling, but OpenAI's strict response_format schema rejects it.
        structured_outputs = build_task_planner_structured_outputs(
            planner_llm=planner_llm,
            semantic_router_llm=self.semantic_router_llm,
            interrupt_llm=self.interrupt_llm,
        )
        self.structured_planner = structured_outputs.planner
        self._structured_planner_by_response_type: dict[type[PlannerOutput], object] = {
            PlannerOutput: self.structured_planner
        }

        self.structured_interrupt_router = structured_outputs.interrupt_router
        self.structured_quoted_replay = structured_outputs.quoted_replay
        self.structured_context_frame_followup = structured_outputs.context_frame_followup
        self.structured_context_frame_replay_modifier = structured_outputs.context_frame_replay_modifier
        self.structured_pending_action_edit = structured_outputs.pending_action_edit
        self.structured_batch_slot_patch = structured_outputs.batch_slot_patch
        self.structured_confirmation_decision = structured_outputs.confirmation_decision

        self.task_state_service = task_state_service
        if not self.uses_dedicated_interrupt_model:
            logger.warning("interrupt_router_model_not_dedicated", mode="planner_fallback")
        if not self.uses_dedicated_semantic_router_model:
            logger.warning("semantic_router_model_not_dedicated", mode="interrupt_or_planner_fallback")

    def _structured_planner_for_response_type(self, response_type: type[PlannerOutput]) -> object:
        structured_planner = self._structured_planner_by_response_type.get(response_type)
        if structured_planner is None:
            structured_planner = with_structured_output(
                self.planner_llm,
                response_type,
                method="function_calling",
            )
            self._structured_planner_by_response_type[response_type] = structured_planner
        return structured_planner

    async def plan_tasks_with_quality(
        self,
        phone_number: str,
        text: str,
        *,
        context: str = "None",
        prompt_signals: prompt_models.PlannerPromptSignals,
        path_label: str = "planner_path",
    ) -> PlannerPlanResult:
        """Plan tasks and return raw output diagnostics."""
        effective_context = _augment_context_with_clean_transfer_hint(context, text, prompt_signals)
        user_prompt = PLANNER_USER_PROMPT_TEMPLATE.format(
            phone_number=phone_number,
            user_message=text,
            context=effective_context,
        )
        prompt_input = prompt_models.PlannerPromptBuildInput(
            text=text,
            context=effective_context,
            signals=prompt_signals,
        )
        prompt_result = build_runtime_planner_system_prompt(prompt_input)
        system_prompt = prompt_result.system_prompt
        response_type = _planner_response_model_for_prompt(prompt_signals, prompt_result)
        raw_model_output = await invoke_structured_prompt(
            self._structured_planner_for_response_type(response_type),
            response_type,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            logger=logger,
            event_name="planner_llm_call",
            model_llm=self.planner_llm,
            path_label=path_label,
            latency_span="planner_llm",
            log_fields={
                "context_chars": len(effective_context),
                "context_mode": "compact" if prompt_signals.compact_context else "full",
                "prompt_profile": prompt_result.profile,
                "prompt_bundles": list(prompt_result.selected_bundle_ids),
                "prompt_rule_count": len(prompt_result.selected_rule_ids),
                "baseline_runtime_system_chars": PLANNER_PROMPT_BASELINE_RESULT.char_count,
                "baseline_runtime_profile": PLANNER_PROMPT_BASELINE_RESULT.profile,
                "planner_response_model": response_type.__name__,
            },
            config=build_llm_runnable_config(
                role="planner",
                phone_number=phone_number,
                path_label=path_label,
                task_domain="orchestrator",
                extra_metadata={"context_mode": "compact" if prompt_signals.compact_context else "full"},
            ),
            prompt_cache_key=_planner_prompt_cache_key(response_type),
        )
        raw_output = PlannerOutput.model_validate(raw_model_output.model_dump())
        normalized_output, quality_report = normalize_planner_transaction_output_with_quality(
            raw_output.model_copy(deep=True),
            text,
        )
        return PlannerPlanResult(
            raw_output=raw_output,
            planner_output=normalized_output,
            quality_report=quality_report,
        )

    async def route_pending_input(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "interrupt_path",
        prompt_mode: str = "full",
    ) -> InterruptRouteDecision:
        """Classify whether pending-input turn should continue current flow or switch intent."""
        user_prompt = interrupt_prompts.INTERRUPT_ROUTER_USER_PROMPT_TEMPLATE.format(
            phone_number=phone_number,
            user_message=text,
            context=context,
        )
        system_prompt = (
            interrupt_prompts.INTERRUPT_ROUTER_SYSTEM_PROMPT_COMPACT
            if prompt_mode == "compact"
            else interrupt_prompts.INTERRUPT_ROUTER_SYSTEM_PROMPT_FULL
        )
        return await invoke_structured_prompt(
            self.structured_interrupt_router,
            InterruptRouteDecision,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            logger=logger,
            event_name="interrupt_router_llm_call",
            model_llm=self.interrupt_llm,
            path_label=path_label,
            latency_span="interrupt_router_llm",
            log_fields={
                "context_chars": len(context),
                "context_mode": "compact" if context == "None" else "full",
                "prompt_mode": prompt_mode,
            },
            config=build_llm_runnable_config(
                role="interrupt_router",
                phone_number=phone_number,
                path_label=path_label,
                task_domain="interrupt",
                extra_metadata={"prompt_mode": prompt_mode},
            ),
        )

    async def classify_confirmation_reply(
        self,
        text: str,
        *,
        prompt_kind: ConfirmationPromptKind,
        locale: str | None = None,
        context: str = "None",
        path_label: str = "interrupt_path",
    ) -> ConfirmationDecision:
        """Bounded LLM fallback for prompt-scoped approval/rejection replies."""
        start = time.perf_counter()
        structured_llm = self.structured_confirmation_decision.with_config(
            build_llm_runnable_config(
                role="interrupt_router",
                path_label=path_label,
                task_domain="confirmation",
                locale=locale,
                extra_metadata={"prompt_kind": prompt_kind},
            )
        )
        result = await classify_confirmation_reply(
            text,
            prompt_kind=prompt_kind,
            locale=locale,
            context=context,
            structured_llm=structured_llm,
        )
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "confirmation_decision_llm_call",
            duration_ms=round(duration_ms, 2),
            model=model_name(self.interrupt_llm),
            action=result.action,
            source=result.source,
            confidence=result.confidence,
            prompt_kind=prompt_kind,
            context_chars=len(context),
        )
        output_metrics = structured_output_metrics(result)
        record_llm_call(
            event_name="confirmation_decision_llm_call",
            duration_ms=duration_ms,
            model=model_name(self.interrupt_llm),
            response_type=type(result).__name__,
            system_chars=len(context),
            user_chars=len(text),
            output_json_chars=output_metrics.get("output_json_chars"),
            output_token_estimate=output_metrics.get("output_token_estimate"),
            extra_fields={
                **output_metrics,
                "prompt_kind": prompt_kind,
                "context_chars": len(context),
                "source": result.source,
                "action": result.action,
                "confidence": result.confidence,
            },
        )
        log_latency_span(logger, span="confirmation_decision_llm", duration_ms=duration_ms, path_label=path_label)
        return result

    async def interpret_context_frame_followup(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "planner_path",
    ) -> ContextFrameFollowupDecision:
        """Classify whether a user turn is a semantic follow-up to the latest displayed frame."""
        user_prompt = context_frame_prompts.CONTEXT_FRAME_FOLLOWUP_USER_PROMPT_TEMPLATE.format(
            phone_number=phone_number,
            user_message=text,
            context=context,
        )
        system_prompt = context_frame_prompts.CONTEXT_FRAME_FOLLOWUP_SYSTEM_PROMPT
        return await invoke_structured_prompt(
            self.structured_context_frame_followup,
            ContextFrameFollowupDecision,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            logger=logger,
            event_name="context_frame_followup_llm_call",
            model_llm=self.semantic_router_llm,
            path_label=path_label,
            latency_span="context_frame_followup_llm",
            log_fields={
                "context_chars": len(context),
                "context_mode": "compact" if context == "None" else "full",
            },
            config=build_llm_runnable_config(
                role="context_frame_followup",
                phone_number=phone_number,
                path_label=path_label,
                task_domain="query",
            ),
        )

    async def extract_context_frame_replay_modifiers(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "planner_path",
    ) -> ContextFrameReplayModifier:
        """Extract a strict edit patch for frame-backed transaction replay."""
        user_prompt = context_frame_prompts.CONTEXT_FRAME_REPLAY_MODIFIER_USER_PROMPT_TEMPLATE.format(
            phone_number=phone_number,
            user_message=text,
            context=context,
        )
        system_prompt = context_frame_prompts.CONTEXT_FRAME_REPLAY_MODIFIER_SYSTEM_PROMPT
        return await invoke_structured_prompt(
            self.structured_context_frame_replay_modifier,
            ContextFrameReplayModifier,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            logger=logger,
            event_name="context_frame_replay_modifier_llm_call",
            model_llm=self.semantic_router_llm,
            path_label=path_label,
            latency_span="context_frame_replay_modifier_llm",
            log_fields={
                "context_chars": len(context),
                "context_mode": "compact" if context == "None" else "full",
            },
            config=build_llm_runnable_config(
                role="context_frame_replay_modifier",
                phone_number=phone_number,
                path_label=path_label,
                task_domain="query",
            ),
        )

    async def interpret_pending_action_edit(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "interrupt_path",
    ) -> PendingActionEditDecision:
        """Classify a user turn as a semantic edit to pending confirmation tasks."""
        user_prompt = interrupt_prompts.PENDING_ACTION_EDIT_USER_PROMPT_TEMPLATE.format(
            phone_number=phone_number,
            user_message=text,
            context=context,
        )
        system_prompt = interrupt_prompts.PENDING_ACTION_EDIT_SYSTEM_PROMPT
        return await invoke_structured_prompt(
            self.structured_pending_action_edit,
            PendingActionEditDecision,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            logger=logger,
            event_name="pending_action_edit_llm_call",
            model_llm=self.interrupt_llm,
            path_label=path_label,
            latency_span="pending_action_edit_llm",
            log_fields={
                "context_chars": len(context),
                "context_mode": "compact" if context == "None" else "full",
            },
            config=build_llm_runnable_config(
                role="interrupt_router",
                phone_number=phone_number,
                path_label=path_label,
                task_domain="pending_action",
            ),
        )

    async def interpret_batch_slot_patch(
        self,
        phone_number: str,
        text: str,
        context: str = "None",
        *,
        path_label: str = "interrupt_path",
    ) -> BatchSlotPatchDecision:
        """Extract scoped slot updates for an active pre-auth transaction batch."""
        user_prompt = interrupt_prompts.BATCH_SLOT_PATCH_USER_PROMPT_TEMPLATE.format(
            phone_number=phone_number,
            user_message=text,
            context=context,
        )
        return await invoke_structured_prompt(
            self.structured_batch_slot_patch,
            BatchSlotPatchDecision,
            system_prompt=interrupt_prompts.BATCH_SLOT_PATCH_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            logger=logger,
            event_name="batch_slot_patch_llm_call",
            model_llm=self.interrupt_llm,
            path_label=path_label,
            latency_span="batch_slot_patch_llm",
            log_fields={
                "context_chars": len(context),
                "context_mode": "compact" if context == "None" else "full",
            },
            config=build_llm_runnable_config(
                role="interrupt_router",
                phone_number=phone_number,
                path_label=path_label,
                task_domain="batch_slot_patch",
            ),
        )

    async def interpret_quoted_replay(
        self, phone_number: str, text: str, context: str = "None"
    ) -> QuotedReplayInterpretation:
        """Interpret a quoted follow-up turn for replay semantics."""
        user_prompt = quoted_replay_prompts.QUOTED_REPLAY_USER_PROMPT_TEMPLATE.format(
            phone_number=phone_number,
            user_message=text,
            context=context,
        )
        system_prompt = quoted_replay_prompts.QUOTED_REPLAY_SYSTEM_PROMPT
        parsed = await invoke_structured_prompt(
            self.structured_quoted_replay,
            QuotedReplayInterpretation,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            logger=logger,
            event_name="quoted_replay_llm_call",
            model_llm=self.planner_llm,
            config=build_llm_runnable_config(
                role="planner",
                phone_number=phone_number,
                path_label="quoted_replay",
                task_domain="quoted_replay",
            ),
        )
        logger.info(
            "quoted_replay_decision",
            decision=parsed.decision,
            confidence=parsed.confidence,
            detected_language=parsed.detected_language,
            tasks=len(parsed.tasks),
        )
        return parsed


__all__ = [
    "PLANNER_USER_PROMPT_TEMPLATE",
    "TaskPlanner",
]
