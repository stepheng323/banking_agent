import re
from typing import Any, cast

from apps.chat.src.agent.orchestrator.capabilities.unsupported_capability_detection import (
    detect_unsupported_capability,
)
from apps.chat.src.agent.orchestrator.context.frame_manager import ContextFrameManager
from apps.chat.src.agent.orchestrator.context.models import ContextFrame, ContextFrameType
from apps.chat.src.agent.orchestrator.conversation.conversation_responder_text import (
    is_contextual_casual_followup_turn,
)
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.turn_directive import RouteResolution
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.casual import (
    looks_like_obvious_casual_or_meta_turn,
)
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.direct_domains import (
    _is_account_balance_request,
    _is_account_domain_request,
    _is_beneficiary_domain_request,
    _is_query_domain_request,
)
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.transaction_intents import (
    _classify_obvious_transfer_request,
    _is_obvious_airtime_request,
    _is_obvious_data_request,
    _obvious_mixed_transaction_executors,
)
from apps.chat.src.agent.orchestrator.workflows.gate.core.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.core.outcomes import direct_response, task_dispatch
from apps.chat.src.agent.orchestrator.workflows.gate.state.locale_state import _current_locale
from apps.chat.src.agent.orchestrator.workflows.gate.utils.direct_tasks import (
    _build_direct_domain_task,
    _next_direct_domain_task_id,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_followup_surface_engine import (
    build_surface_answer_response as build_context_frame_followup_response,
)
from apps.chat.src.agent.orchestrator.workflows.planner.context.frames.context_frame_followup_types import (
    ContextFrameFollowupResponse,
)
from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.services.reasoning.shortcuts import resolve_query_shortcut
from shared.types.balance import (
    BalanceConversationState,
    BalanceFollowupDelta,
    BalanceQueryContract,
    apply_balance_followup,
    initial_balance_contract,
)
from shared.types.conversation_sets import (
    AccountLifecycleContract,
    AccountLifecycleOperation,
    BeneficiaryOperation,
    BeneficiaryQueryContract,
    ConversationSetState,
    ScheduleOperation,
    ScheduleQueryContract,
    apply_set_scope,
    resolve_delta_references,
)
from shared.types.planner import ContextFrameFollowupDecision
from shared.types.read import ReadRequest
from shared.utils.bank_aliases import display_bank_name, is_known_bank_alias
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_CONTEXT_FRAME_REPLAY_CUE_RE = re.compile(
    r"\b(?:again|redo|repeat|replay|rerun|resend|same\s+again|send\s+again|"
    r"encore|repete|tun\s+se|tun|sake|maimaita|ziga)\b",
    re.IGNORECASE,
)
_CONTEXT_FRAME_DISPLAY_CUE_RE = re.compile(
    r"(?iu)(?:"
    r"\b(?:show|view|see|display|open|list|details?|more|fetch)\b|"
    r"\b(?:montre|voir|affiche|muestra|mostrar|ver)\b|"
    r"\b(?:fihan|wo|nuna|gani|gosi|lee)\b"
    r")"
)
_READ_ONLY_REFRESH_CUE_RE = re.compile(
    r"(?iu)^(?:"
    r"(?:fetch|refresh|reload|recheck)(?:\s+(?:it|this|them|those|again|againo|same|list|result|results))*|"
    r"check(?:\s+(?:it|this|them|those|same))?\s+againo?|"
    r"(?:show|display|list|view)\s+(?:it|this|them|those|same|list|result|results)(?:\s+againo?)?|"
    r"(?:run|try)\s+(?:it|this|them|those|same)\s+againo?"
    r")$"
)
_MONEY_MOVE_REFRESH_BLOCK_RE = re.compile(
    r"(?iu)\b(?:send|transfer|pay|buy|airtime|data|bundle|recharge|top\s*up|do|redo|resend)\b"
)


def _is_fresh_transaction_command(ctx: GateContext) -> bool:
    if not ctx.phrase_heavy_fastpath_allowed:
        return False
    if _obvious_mixed_transaction_executors(ctx.message_text):
        return True
    if _classify_obvious_transfer_request(ctx.message_text) is not None:
        return True
    return _is_obvious_airtime_request(ctx.message_text) or _is_obvious_data_request(ctx.message_text)


def _looks_like_context_frame_replay(text: str) -> bool:
    return bool(_CONTEXT_FRAME_REPLAY_CUE_RE.search(text or ""))


def _looks_like_context_frame_display_request(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if not normalized or len(normalized) > 80:
        return False
    if re.search(r"\bmy\b", normalized, re.IGNORECASE):
        return False
    if re.search(r"(?:₦|ngn|\d)", normalized, re.IGNORECASE):
        return False
    return bool(_CONTEXT_FRAME_DISPLAY_CUE_RE.search(normalized))


def _looks_like_terse_context_frame_followup(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if not normalized:
        return False
    if len(normalized) > 80:
        return False
    tokens = re.findall(r"[\w']+", normalized, re.UNICODE)
    return len(tokens) <= 4


def _looks_like_read_only_refresh_request(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if not normalized or len(normalized) > 80:
        return False
    if re.search(r"(?:₦|ngn|\d)", normalized, re.IGNORECASE):
        return False
    if _MONEY_MOVE_REFRESH_BLOCK_RE.search(normalized):
        return False
    return bool(_READ_ONLY_REFRESH_CUE_RE.search(normalized))


def _context_frame_followup_updates(
    ctx: GateContext,
    frame_followup: ContextFrameFollowupResponse,
) -> RouteResolution:
    extra_updates: dict[str, Any] = {
        "context_frames": frame_followup.context_frames or ctx.state_view.context_frames,
    }
    if frame_followup.response:
        extra_updates["final_response"] = frame_followup.response
    if frame_followup.tasks and frame_followup.waves:
        return task_dispatch(
            ctx,
            tasks=frame_followup.tasks,
            waves=frame_followup.waves,
            owner="semantic_router",
            decision="context_frame_followup",
            path_shape=frame_followup.path_shape,
            extra_updates=extra_updates,
            target_domain=frame_followup.recent_domain_focus,
            mode="continuation",
            source="context_frame_followup",
        )
    return direct_response(
        ctx,
        response=frame_followup.response or "",
        owner="semantic_router",
        decision="context_frame_followup",
        path_shape=frame_followup.path_shape,
        extra_updates=extra_updates,
        target_domain=frame_followup.recent_domain_focus,
        mode="continuation",
        source="context_frame_followup",
    )


def _build_read_only_refresh_spec(ctx: GateContext, frame: ContextFrame | None) -> tuple[str, TaskSpec, str] | None:
    if frame is not None:
        raw_read_request = frame.metadata.get("read_request")
        if isinstance(raw_read_request, dict):
            try:
                retained = ReadRequest.model_validate(raw_read_request)
            except ValueError:
                retained = None
            if retained is not None and retained.subject != "transaction":
                shape = retained.response_shape
                if shape.startswith("fact_"):
                    shape = "surface_list"
                request = retained.model_copy(update={"response_shape": shape, "offset": 0})
                domain_by_subject = {
                    "balance": "account",
                    "linked_account": "account",
                    "default_account": "account",
                    "beneficiary": "beneficiary",
                    "schedule": "schedule",
                    "ticket": "support",
                    "receipt": "support",
                }
                domain = domain_by_subject.get(request.subject)
                if domain is not None:
                    try:
                        balance_contract = (
                            BalanceQueryContract.model_validate(frame.metadata.get("balance_contract"))
                            if request.subject == "balance" and isinstance(frame.metadata.get("balance_contract"), dict)
                            else None
                        )
                        beneficiary_contract = (
                            BeneficiaryQueryContract.model_validate(frame.metadata.get("beneficiary_contract"))
                            if request.subject == "beneficiary"
                            and isinstance(frame.metadata.get("beneficiary_contract"), dict)
                            else None
                        )
                        schedule_contract = (
                            ScheduleQueryContract.model_validate(frame.metadata.get("schedule_contract"))
                            if request.subject == "schedule"
                            and isinstance(frame.metadata.get("schedule_contract"), dict)
                            else None
                        )
                        lifecycle_contract = (
                            AccountLifecycleContract.model_validate(frame.metadata.get("account_lifecycle_contract"))
                            if request.subject in {"linked_account", "default_account"}
                            and isinstance(frame.metadata.get("account_lifecycle_contract"), dict)
                            else None
                        )
                    except ValueError:
                        balance_contract = None
                        beneficiary_contract = None
                        schedule_contract = None
                        lifecycle_contract = None
                    required_contract = {
                        "balance": balance_contract,
                        "beneficiary": beneficiary_contract,
                        "schedule": schedule_contract,
                        "linked_account": lifecycle_contract,
                        "default_account": lifecycle_contract,
                    }.get(request.subject, True)
                    if required_contract is None:
                        return None
                    task_id, spec = _build_direct_domain_task(
                        state_view=ctx.state_view,
                        domain=cast(Any, domain),
                        mode="continuation",
                        read_request=request,
                        balance_contract=balance_contract,
                        beneficiary_contract=beneficiary_contract,
                        schedule_contract=schedule_contract,
                        account_lifecycle_contract=lifecycle_contract,
                    )
                    raw_set_state = frame.metadata.get("conversation_set_state")
                    if request.subject in {"beneficiary", "schedule", "linked_account", "default_account"}:
                        if not isinstance(raw_set_state, dict):
                            return None
                        try:
                            set_state = ConversationSetState.model_validate(raw_set_state)
                            spec.payload["conversation_set_state"] = set_state.model_dump(
                                mode="json",
                                exclude_none=True,
                            )
                        except ValueError:
                            return None
                    return task_id, spec, f"read_{request.subject}"

        if frame.frame_type == ContextFrameType.TRANSACTION_LIST and _is_query_surface_frame(frame):
            task_id = _next_direct_domain_task_id(ctx.state_view.tasks, "query")
            payload = {
                "message": ctx.message_text,
                "instruction": ctx.message_text,
            }
            return task_id, TaskSpec(id=task_id, type="query", stage=TaskStage.DRAFT, payload=payload), "repeat_query"

    return None


def _expired_set_context_response(ctx: GateContext, *, domain: str) -> RouteResolution:
    return direct_response(
        ctx,
        response=render_message("conversation_set.stale_selection", _current_locale(ctx.state_view)),
        owner="semantic_router",
        decision="conversation_set_expired",
        target_domain=domain,
        mode="continuation",
        source="context_frame_followup",
        path_shape="conversation_set_expired",
    )


def _typed_read_subject_pivot_updates(
    ctx: GateContext,
    decision: ContextFrameFollowupDecision,
) -> RouteResolution | None:
    """Dispatch a fully typed read pivot without a second semantic call."""
    subject = decision.read_subject
    shape = decision.read_response_shape
    if subject is None or shape is None or subject == "transaction":
        return None

    filters = decision.filters
    bank_name = filters.bank if filters is not None else None
    if bank_name is None and is_known_bank_alias(decision.target_text):
        bank_name = display_bank_name(decision.target_text)
    entity_name = filters.counterparty if filters is not None else None
    status = filters.status if filters is not None else None
    if subject == "beneficiary" and decision.beneficiary_delta is not None:
        entity_name = decision.beneficiary_delta.entity_name or entity_name
        bank_name = decision.beneficiary_delta.bank_name or bank_name

    try:
        request = ReadRequest(
            subject=subject,
            response_shape=cast(Any, shape),
            entity_name=entity_name,
            bank_name=bank_name,
            status=status,
        )
    except ValueError:
        return None

    domain_by_subject = {
        "balance": "account",
        "linked_account": "account",
        "default_account": "account",
        "beneficiary": "beneficiary",
        "schedule": "schedule",
        "ticket": "support",
        "receipt": "support",
    }
    domain = domain_by_subject.get(subject)
    if domain is None:
        return None

    balance_contract = None
    beneficiary_contract = None
    schedule_contract = None
    lifecycle_contract = None
    if subject == "balance":
        balance_contract = initial_balance_contract(bank_name=bank_name, response_shape=shape)
    elif subject == "beneficiary":
        beneficiary_operation: BeneficiaryOperation = "list"
        if shape == "fact_bool":
            beneficiary_operation = "existence"
        elif shape == "fact_count":
            beneficiary_operation = "count"
        elif shape == "surface_detail":
            beneficiary_operation = "detail"
        beneficiary_contract = BeneficiaryQueryContract(
            operation=beneficiary_operation,
            response_shape=shape,
            entity_name=entity_name,
            bank_name=bank_name,
            beneficiary_type=(
                decision.beneficiary_delta.beneficiary_type if decision.beneficiary_delta is not None else None
            ),
        )
    elif subject == "schedule":
        schedule_operation: ScheduleOperation = "list"
        if shape == "fact_bool":
            schedule_operation = "existence"
        elif shape == "fact_count":
            schedule_operation = "count"
        elif shape == "surface_detail":
            schedule_operation = "detail"
        schedule_contract = ScheduleQueryContract(
            operation=schedule_operation,
            response_shape=shape,
            recipient_name=entity_name,
            statuses=[status] if status else [],
        )
    elif subject in {"linked_account", "default_account"}:
        lifecycle_operation: AccountLifecycleOperation = "default_identity" if subject == "default_account" else "list"
        if subject == "linked_account":
            if shape == "fact_bool":
                lifecycle_operation = "existence"
            elif shape == "fact_count":
                lifecycle_operation = "count"
            elif shape == "fact_status":
                lifecycle_operation = "readiness"
            elif shape == "surface_detail":
                lifecycle_operation = "detail"
        lifecycle_contract = AccountLifecycleContract(
            operation=lifecycle_operation,
            response_shape=shape,
            bank_name=bank_name,
            mandate_statuses=[status] if status else [],
        )

    task_id, spec = _build_direct_domain_task(
        state_view=ctx.state_view,
        domain=cast(Any, domain),
        mode="new",
        read_request=request,
        balance_contract=balance_contract,
        beneficiary_contract=beneficiary_contract,
        schedule_contract=schedule_contract,
        account_lifecycle_contract=lifecycle_contract,
    )
    logger.info(
        "canonical_read_subject_pivot_resolved",
        subject=subject,
        response_shape=shape,
        has_bank_filter=bool(bank_name),
        has_entity_filter=bool(entity_name),
        has_status_filter=bool(status),
    )
    return task_dispatch(
        ctx,
        tasks={task_id: spec},
        waves=[[task_id]],
        owner="semantic_router",
        decision="canonical_read_subject_pivot",
        target_domain=domain,
        mode="new",
        source="context_frame_followup",
        path_shape="canonical_read_subject_pivot",
    )


def _read_contract_followup_updates(
    ctx: GateContext,
    frame: ContextFrame,
    decision: ContextFrameFollowupDecision,
) -> RouteResolution | None:
    raw = frame.metadata.get("read_request")
    if not isinstance(raw, dict):
        return None
    try:
        retained = ReadRequest.model_validate(raw)
    except ValueError:
        return None
    if decision.read_subject is not None and decision.read_subject != retained.subject:
        return _typed_read_subject_pivot_updates(ctx, decision)
    if retained.subject in {"transaction", "balance"}:
        return None

    updates: dict[str, Any] = {}
    filter_delta_applied = False
    beneficiary_type_update: str | None = None
    if decision.page_action == "next":
        updates["offset"] = retained.offset + retained.page_size
    elif decision.page_action == "previous":
        updates["offset"] = max(0, retained.offset - retained.page_size)
    elif decision.page_action == "first":
        updates["offset"] = 0

    filters = decision.filters
    if filters is not None:
        if retained.subject in {"balance", "linked_account"} and filters.bank:
            updates["bank_name"] = filters.bank
            updates["offset"] = 0
            filter_delta_applied = True
        if retained.subject in {"linked_account", "schedule", "ticket"} and filters.status:
            updates["status"] = filters.status
            updates["offset"] = 0
            filter_delta_applied = True
        if retained.subject == "beneficiary" and filters.counterparty:
            updates["entity_name"] = filters.counterparty
            updates["offset"] = 0
            filter_delta_applied = True

    # A named membership lookup refines the retained repository read; it is not
    # a selection restricted to rows in the last frame. Fact-only frames carry
    # no beneficiary rows, so promote the interpreter's typed lookup target into
    # the canonical filter and existence shape.
    if (
        retained.subject == "beneficiary"
        and decision.decision in {"lookup_entity", "entity_lookup"}
        and isinstance(decision.target_text, str)
        and decision.target_text.strip()
    ):
        updates.setdefault("entity_name", decision.target_text.strip())
        updates.setdefault("response_shape", "fact_bool")
        updates["offset"] = 0
        filter_delta_applied = True

    # The semantic interpreter may place an elliptical account target in
    # ``target_text`` while classifying the turn as a lookup. Promote only a
    # recognized typed bank target; never infer a bank from the raw message.
    if (
        retained.subject in {"balance", "linked_account"}
        and "bank_name" not in updates
        and is_known_bank_alias(decision.target_text)
    ):
        bank_name = display_bank_name(decision.target_text)
        if bank_name is not None:
            updates["bank_name"] = bank_name
            updates["offset"] = 0
            filter_delta_applied = True

    if decision.read_response_shape is not None:
        updates["response_shape"] = decision.read_response_shape
    elif (
        not filter_delta_applied
        and decision.decision in {"show_details", "detail_request"}
        and retained.response_shape.startswith("fact_")
    ):
        updates["response_shape"] = (
            "surface_list" if retained.subject in {"linked_account", "beneficiary", "schedule"} else "surface_detail"
        )
        updates["offset"] = 0

    beneficiary_delta = decision.beneficiary_delta if retained.subject == "beneficiary" else None
    if beneficiary_delta is not None:
        shape_by_operation = {
            "count": "fact_count",
            "existence": "fact_bool",
            "list": "surface_list",
            "detail": "surface_detail",
        }
        if beneficiary_delta.operation != "preserve":
            updates["response_shape"] = shape_by_operation[beneficiary_delta.operation]
            updates["offset"] = 0
        if beneficiary_delta.entity_name is not None:
            updates["entity_name"] = beneficiary_delta.entity_name
            updates["offset"] = 0
            filter_delta_applied = True
        if beneficiary_delta.bank_name is not None:
            updates["bank_name"] = beneficiary_delta.bank_name
            updates["offset"] = 0
            filter_delta_applied = True
        beneficiary_type_update = beneficiary_delta.beneficiary_type

    lifecycle_delta = (
        decision.account_lifecycle_delta if retained.subject in {"linked_account", "default_account"} else None
    )
    if lifecycle_delta is not None:
        lifecycle_shape_by_operation = {
            "count": "fact_count",
            "existence": "fact_bool",
            "list": "surface_list",
            "detail": "surface_detail",
            "readiness": "fact_status",
            "default_identity": "fact_value",
        }
        if lifecycle_delta.operation != "preserve":
            updates["response_shape"] = lifecycle_shape_by_operation[lifecycle_delta.operation]
            updates["offset"] = 0
        if lifecycle_delta.bank_scope == "all":
            updates["bank_name"] = None
            updates["offset"] = 0
            filter_delta_applied = True
        elif lifecycle_delta.bank_scope == "named":
            updates["bank_name"] = lifecycle_delta.bank_name
            updates["offset"] = 0
            filter_delta_applied = True

    if not updates and decision.set_scope_delta is None and beneficiary_type_update is None and lifecycle_delta is None:
        return None

    request = retained.model_copy(update=updates)
    domain_by_subject = {
        "balance": "account",
        "linked_account": "account",
        "default_account": "account",
        "beneficiary": "beneficiary",
        "schedule": "schedule",
        "ticket": "support",
        "receipt": "support",
    }
    domain = domain_by_subject.get(request.subject)
    if domain is None:
        return None
    beneficiary_contract: BeneficiaryQueryContract | None = None
    schedule_contract: ScheduleQueryContract | None = None
    lifecycle_contract: AccountLifecycleContract | None = None
    contract_key = {
        "beneficiary": "beneficiary_contract",
        "schedule": "schedule_contract",
        "linked_account": "account_lifecycle_contract",
        "default_account": "account_lifecycle_contract",
    }.get(request.subject)
    raw_contract = frame.metadata.get(contract_key) if contract_key else None
    if contract_key is not None and not isinstance(raw_contract, dict):
        return _expired_set_context_response(ctx, domain=domain)
    try:
        if request.subject == "beneficiary":
            parsed_beneficiary_contract = BeneficiaryQueryContract.model_validate(raw_contract)
            beneficiary_operation = {
                "fact_bool": "existence",
                "fact_count": "count",
                "surface_list": "list",
                "surface_paginated": "list",
                "surface_detail": "detail",
            }.get(request.response_shape, parsed_beneficiary_contract.operation)
            beneficiary_contract = parsed_beneficiary_contract.model_copy(
                update={
                    "operation": beneficiary_operation,
                    "response_shape": request.response_shape,
                    "entity_name": request.entity_name,
                    "bank_name": request.bank_name,
                    "beneficiary_type": (
                        beneficiary_type_update
                        if beneficiary_type_update is not None
                        else parsed_beneficiary_contract.beneficiary_type
                    ),
                    "scope": decision.set_scope_delta or parsed_beneficiary_contract.scope,
                }
            )
        elif request.subject == "schedule":
            parsed_schedule_contract = ScheduleQueryContract.model_validate(raw_contract)
            schedule_contract = parsed_schedule_contract.model_copy(
                update={
                    "operation": (
                        "list"
                        if request.response_shape in {"surface_list", "surface_paginated"}
                        else "detail"
                        if request.response_shape == "surface_detail"
                        else parsed_schedule_contract.operation
                    ),
                    "response_shape": request.response_shape,
                    "statuses": [request.status] if request.status else [],
                    "scope": decision.set_scope_delta or parsed_schedule_contract.scope,
                }
            )
        elif request.subject in {"linked_account", "default_account"}:
            parsed_lifecycle_contract = AccountLifecycleContract.model_validate(raw_contract)
            lifecycle_operation_by_shape = {
                "fact_bool": "existence",
                "fact_count": "count",
                "fact_status": "readiness",
                "fact_value": "default_identity",
                "surface_list": "list",
                "surface_paginated": "list",
                "surface_detail": "detail",
            }
            lifecycle_contract = parsed_lifecycle_contract.model_copy(
                update={
                    "operation": lifecycle_operation_by_shape.get(
                        request.response_shape,
                        parsed_lifecycle_contract.operation,
                    ),
                    "response_shape": request.response_shape,
                    "bank_name": request.bank_name,
                    "mandate_statuses": [request.status] if request.status else [],
                    "scope": decision.set_scope_delta or parsed_lifecycle_contract.scope,
                }
            )
    except ValueError:
        return _expired_set_context_response(ctx, domain=domain)
    logger.info(
        "canonical_read_followup_resolved",
        subject=request.subject,
        response_shape=request.response_shape,
        bank_filter_changed="bank_name" in updates,
        bank_filter_source=(
            "structured_filter"
            if filters is not None and filters.bank
            else "typed_target"
            if "bank_name" in updates
            else None
        ),
        status_filter_changed="status" in updates,
        entity_filter_changed="entity_name" in updates,
        page_action=decision.page_action,
    )
    task_id, spec = _build_direct_domain_task(
        state_view=ctx.state_view,
        domain=cast(Any, domain),
        mode="continuation",
        read_request=request,
        beneficiary_contract=beneficiary_contract,
        schedule_contract=schedule_contract,
        account_lifecycle_contract=lifecycle_contract,
    )
    raw_set_state = frame.metadata.get("conversation_set_state")
    if decision.set_scope_delta is not None:
        if not isinstance(raw_set_state, dict):
            return _expired_set_context_response(ctx, domain=domain)
        try:
            set_state = ConversationSetState.model_validate(raw_set_state)
            visible_refs = list(set_state.last_result_refs)
            resolved, unresolved = resolve_delta_references(
                decision.set_scope_delta,
                visible_refs=visible_refs,
            )
            selected = apply_set_scope(
                set_state,
                decision.set_scope_delta,
                resolved_refs=resolved,
                all_refs=visible_refs,
            )
            if unresolved or not selected:
                return direct_response(
                    ctx,
                    response=render_message(
                        "query.clarify.which_one",
                        _current_locale(ctx.state_view),
                        {"context_suffix": ""},
                    ),
                    owner="semantic_router",
                    decision="conversation_set_clarification",
                    target_domain=domain,
                    mode="continuation",
                    source="context_frame_followup",
                    path_shape="conversation_set_clarification",
                )
            next_state = set_state.model_copy(
                update={
                    "focused_ref": selected[0] if len(selected) == 1 else None,
                    "last_result_refs": selected,
                    "operation": str(request.response_shape),
                }
            )
            spec.payload["conversation_set_state"] = next_state.model_dump(mode="json", exclude_none=True)
            spec.payload["selected_entity_ids"] = [ref.entity_id for ref in selected]
            logger.info(
                "conversation_set_scope_resolved",
                domain=set_state.domain,
                scope_operation=decision.set_scope_delta.operation,
                selected_count=len(selected),
                stale_count=0,
            )
        except ValueError:
            return _expired_set_context_response(ctx, domain=domain)
    return task_dispatch(
        ctx,
        tasks={task_id: spec},
        waves=[[task_id]],
        owner="semantic_router",
        decision="canonical_read_followup",
        path_shape="canonical_read_followup",
        target_domain=domain,
        mode="continuation",
        source="context_frame_followup",
    )


def _balance_contract_followup_updates(
    ctx: GateContext,
    frame: ContextFrame,
    decision: ContextFrameFollowupDecision,
) -> RouteResolution | None:
    raw_read = frame.metadata.get("read_request")
    if not isinstance(raw_read, dict):
        return None
    try:
        retained_read = ReadRequest.model_validate(raw_read)
    except ValueError:
        return None
    if retained_read.subject != "balance":
        return None

    raw_contract = frame.metadata.get("balance_contract")
    if not isinstance(raw_contract, dict):
        return None
    try:
        contract = BalanceQueryContract.model_validate(raw_contract)
    except ValueError:
        return None

    raw_state = frame.metadata.get("balance_conversation_state")
    if not isinstance(raw_state, dict):
        return None
    try:
        conversation = BalanceConversationState.model_validate(raw_state)
    except ValueError:
        return None

    delta = decision.balance_delta
    semantic_bank = decision.filters.bank if decision.filters is not None else None
    if not semantic_bank and is_known_bank_alias(decision.target_text):
        semantic_bank = display_bank_name(decision.target_text)
    if semantic_bank:
        operation = delta.operation if delta is not None else "value"
        response_shape = delta.response_shape if delta is not None else "fact_value"
        delta = BalanceFollowupDelta(
            scope_operation="replace",
            bank_names=[semantic_bank],
            operation=operation,
            response_shape=response_shape,
        )
    if delta is None:
        return None

    resolved = apply_balance_followup(contract, conversation, delta)
    if resolved is None:
        logger.info(
            "balance_followup_scope_unresolved",
            scope_operation=delta.scope_operation,
            mentioned_count=len(conversation.mentioned_banks),
            result_count=len(conversation.last_result_banks),
        )
        return direct_response(
            ctx,
            response=render_message("account.balance.scope_clarify", ctx.current_locale),
            owner="semantic_router",
            decision="balance_scope_clarification",
            path_shape="balance_scope_clarification",
            target_domain="account",
            mode="continuation",
            source="context_frame_followup",
        )

    shape = "fact_value" if resolved.operation in {"value", "total"} else "surface_list"
    resolved = resolved.model_copy(update={"response_shape": shape})
    bank_name = resolved.bank_names[0] if resolved.account_scope == "named" and len(resolved.bank_names) == 1 else None
    request = retained_read.model_copy(update={"bank_name": bank_name, "response_shape": shape, "offset": 0})

    mentioned = list(conversation.mentioned_banks)
    for name in resolved.bank_names:
        if name.casefold() not in {item.casefold() for item in mentioned}:
            mentioned.append(name)
    next_state = BalanceConversationState(
        focused_bank=(resolved.bank_names[-1] if len(resolved.bank_names) == 1 else conversation.focused_bank),
        mentioned_banks=mentioned,
        last_result_banks=resolved.bank_names,
        last_operation=resolved.operation,
    )

    task_id, spec = _build_direct_domain_task(
        state_view=ctx.state_view,
        domain="account",
        mode="continuation",
        read_request=request,
        balance_contract=resolved,
    )
    spec.payload["balance_conversation_state"] = next_state.model_dump(mode="json", exclude_none=True)
    logger.info(
        "balance_followup_contract_resolved",
        account_scope=resolved.account_scope,
        operation=resolved.operation,
        selected_count=len(resolved.bank_names),
        mentioned_count=len(next_state.mentioned_banks),
        scope_operation=delta.scope_operation,
    )
    return task_dispatch(
        ctx,
        tasks={task_id: spec},
        waves=[[task_id]],
        owner="semantic_router",
        decision="balance_contract_followup",
        path_shape="balance_contract_followup",
        target_domain="account",
        mode="continuation",
        source="context_frame_followup",
    )


def _is_query_surface_frame(frame: ContextFrame) -> bool:
    source = str(frame.metadata.get("source") or "").strip()
    return source == "query" or frame.frame_id.startswith("query_surface_")


def _read_only_refresh_updates(ctx: GateContext, frame: ContextFrame | None) -> RouteResolution | None:
    refresh_spec = _build_read_only_refresh_spec(ctx, frame)
    if refresh_spec is None:
        return None
    task_id, spec, refresh_action = refresh_spec
    logger.info(
        "gate_read_only_refresh_followup_hit",
        frame_type=frame.frame_type.value if frame is not None else None,
        refresh_action=refresh_action,
        task_id=task_id,
    )
    return task_dispatch(
        ctx,
        tasks={task_id: spec},
        waves=[[task_id]],
        owner="guardrail",
        decision="read_only_refresh_followup",
        path_shape="read_only_refresh_followup",
        extra_updates={"pending_interrupt": None},
        target_domain=spec.type,
        mode="continuation",
        source="context_frame_followup",
        heuristic_type="context_frame_shortcut",
        heuristic_name="read_only_refresh",
    )


def _data_plan_redisplay_updates(ctx: GateContext) -> RouteResolution | None:
    frame_followup = build_context_frame_followup_response(
        ctx.state,
        ctx.message_text,
        decision=ContextFrameFollowupDecision(
            decision="show_details",
            confidence=0.94,
            detected_language=ctx.current_locale,
            reason="short visible-context refresh request",
        ),
        locale=ctx.current_locale,
    )
    if frame_followup is None:
        return None
    return _context_frame_followup_updates(ctx, frame_followup)


def _context_frame_followup_eligible(ctx: GateContext) -> bool:
    return not ctx.live_pending_interrupt and not ctx.state_view.has_gate_blocking_state


def _is_contextual_casual_continuation(ctx: GateContext) -> bool:
    return is_contextual_casual_followup_turn(
        ctx.message_text,
        ctx.state_view.loaded_context_or_empty.get("history"),
    )


def _display_shortcut_followup(ctx: GateContext) -> ContextFrameFollowupResponse | None:
    if not _looks_like_context_frame_display_request(ctx.message_text):
        return None
    return build_context_frame_followup_response(
        ctx.state,
        ctx.message_text,
        decision=ContextFrameFollowupDecision(
            decision="show_details",
            confidence=0.92,
            detected_language=ctx.current_locale,
            reason="short visible-context display request",
        ),
        locale=ctx.current_locale,
    )


def resolve_semantic_context_followup(
    ctx: GateContext,
    decision: ContextFrameFollowupDecision,
    *,
    replay_modifier: Any | None = None,
) -> RouteResolution | None:
    """Materialize one semantic-router frame decision without another LLM call."""
    frame = ContextFrameManager().latest_active_frame(ctx.state)
    if frame is None:
        return None
    balance_followup = _balance_contract_followup_updates(ctx, frame, decision)
    if balance_followup is not None:
        return balance_followup
    read_followup = _read_contract_followup_updates(ctx, frame, decision)
    if read_followup is not None:
        return read_followup
    raw_retained_read = frame.metadata.get("read_request")
    retained_subject = raw_retained_read.get("subject") if isinstance(raw_retained_read, dict) else None
    if decision.read_subject is not None and decision.read_subject != retained_subject:
        logger.info(
            "semantic_context_followup_released_for_read_subject_pivot",
            retained_subject=retained_subject,
            requested_subject=decision.read_subject,
        )
        return None
    if not frame.items:
        # Fact-only read frames intentionally retain a normalized contract but
        # no hidden rows.  They can be rerun through the typed read path above,
        # but cannot safely answer an item-level selector.
        return None
    frame_followup = build_context_frame_followup_response(
        ctx.state,
        ctx.message_text,
        decision=decision,
        replay_modifier=replay_modifier,
        locale=ctx.current_locale,
    )
    logger.info(
        "semantic_context_followup_decision",
        decision=decision.decision,
        confidence=decision.confidence,
        frame_type=frame.frame_type.value,
        item_count=len(frame.items),
        resolved=bool(frame_followup),
    )
    return _context_frame_followup_updates(ctx, frame_followup) if frame_followup is not None else None


async def _stage_context_frame_followup(ctx: GateContext) -> RouteResolution | None:
    """Resolve semantic follow-ups against the latest displayed response frame before domain routing."""
    if not _context_frame_followup_eligible(ctx):
        return None
    raw_pending = ctx.state_view.pending_query_clarification
    if isinstance(raw_pending, dict) and raw_pending.get("pending_input"):
        # Pending query input owns numeric/ordinal/label replies.  Never let
        # the visible frame reinterpret them as a generic historical target.
        logger.info("gate_context_frame_followup_skipped_for_pending_query_input")
        return None
    if looks_like_obvious_casual_or_meta_turn(ctx.message_text):
        logger.info("gate_context_frame_followup_skipped_for_casual_turn")
        return None
    if _is_contextual_casual_continuation(ctx):
        logger.info("gate_context_frame_followup_skipped_for_contextual_casual_turn")
        return None
    if detect_unsupported_capability(ctx.message_text) is not None:
        logger.info("gate_context_frame_followup_skipped_for_unsupported_capability")
        return None

    frame = ContextFrameManager().latest_active_frame(ctx.state)
    if _looks_like_read_only_refresh_request(ctx.message_text):
        if frame is not None and frame.frame_type == ContextFrameType.DATA_PLAN_LIST:
            logger.info(
                "gate_data_plan_redisplay_followup_hit",
                frame_type=frame.frame_type.value,
                item_count=len(frame.items),
            )
            data_plan_followup = _data_plan_redisplay_updates(ctx)
            if data_plan_followup is not None:
                return data_plan_followup

        bypass_read_only = False
        if await ctx.has_active_query_session():
            logger.info("gate_read_only_refresh_bypassed_for_active_query")
            bypass_read_only = True

        if not bypass_read_only:
            read_only_refresh = _read_only_refresh_updates(ctx, frame)
            if read_only_refresh is not None:
                return read_only_refresh

    if await ctx.has_active_query_session():
        logger.info("gate_context_frame_followup_skipped_for_active_query_session")
        return None

    if frame is None or not frame.items:
        return None
    if _is_query_domain_request(ctx.message_text):
        logger.info(
            "gate_context_frame_followup_skipped_for_fresh_query",
            frame_type=frame.frame_type.value,
            item_count=len(frame.items),
        )
        return None
    if (
        _is_account_balance_request(ctx.message_text)
        or _is_account_domain_request(ctx.message_text)
        or _is_beneficiary_domain_request(ctx.message_text)
    ):
        logger.info(
            "gate_context_frame_followup_skipped_for_fresh_read_domain",
            frame_type=frame.frame_type.value,
            item_count=len(frame.items),
        )
        return None
    shortcut = resolve_query_shortcut(ctx.message_text, ctx.current_locale)
    if shortcut is not None and shortcut.kind == "pagination":
        logger.info(
            "gate_context_frame_followup_skipped_for_query_pagination",
            action=shortcut.action,
            frame_type=frame.frame_type.value,
            item_count=len(frame.items),
        )
        return None
    if _is_fresh_transaction_command(ctx):
        logger.info(
            "gate_context_frame_followup_skipped_for_fresh_transaction",
            frame_type=frame.frame_type.value,
            item_count=len(frame.items),
        )
        return None

    if _looks_like_context_frame_display_request(ctx.message_text):
        balance_display = _balance_contract_followup_updates(
            ctx,
            frame,
            ContextFrameFollowupDecision(
                decision="show_details",
                confidence=0.92,
                detected_language=ctx.current_locale,
                balance_delta=BalanceFollowupDelta(
                    scope_operation="last_result",
                    operation="breakdown",
                    response_shape="surface_list",
                ),
                reason="typed balance display continuation",
            ),
        )
        if balance_display is not None:
            return balance_display
        read_followup = _read_contract_followup_updates(
            ctx,
            frame,
            ContextFrameFollowupDecision(
                decision="show_details",
                confidence=0.92,
                detected_language=ctx.current_locale,
                reason="short canonical read display request",
            ),
        )
        if read_followup is not None:
            logger.info(
                "gate_context_frame_display_shortcut_rerouted_to_read_contract",
                frame_type=frame.frame_type.value,
                item_count=len(frame.items),
            )
            return read_followup
        display_followup = _display_shortcut_followup(ctx)
        if display_followup is None:
            return None
        logger.info(
            "gate_context_frame_display_shortcut_hit",
            frame_type=frame.frame_type.value,
            item_count=len(frame.items),
        )
        return _context_frame_followup_updates(ctx, display_followup)

    # Ambiguous follow-ups now proceed to the semantic router.  It returns a
    # compact typed continuation in the same call that chooses the domain.
    logger.info(
        "gate_context_frame_followup_deferred_to_semantic_router",
        frame_type=frame.frame_type.value,
        item_count=len(frame.items),
    )
    return None


__all__ = ["_stage_context_frame_followup", "resolve_semantic_context_followup"]
