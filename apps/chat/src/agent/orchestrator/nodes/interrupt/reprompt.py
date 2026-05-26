from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.nodes.interrupt.context import (
    _cancel_updates,
    _state_locale,
    logger,
)
from apps.chat.src.agent.orchestrator.nodes.interrupt.switch_extract import (
    _build_direct_non_transaction_switch_tasks,
    _build_enriched_transaction_switch_tasks,
)
from apps.chat.src.agent.orchestrator.nodes.interrupt.switch_updates import (
    _build_planner_switch_updates,
    _switch_updates,
)
from apps.chat.src.agent.orchestrator.utils.actionable_payload import build_actionable_payload_for_tasks
from shared.formatters.confirmation import build_confirmation_summary
from shared.formatters.currency import format_naira
from shared.formatters.prompts import format_auth_reason, sanitize_recipient_display_name
from shared.formatters.recipient_display import format_recipient_display_label
from shared.formatters.transaction_copy import build_confirmation_header
from shared.i18n import render_message
from shared.i18n.personality import transfer_personality_context_from_payload
from shared.types.planner import (
    InterruptRouteDecision,
)
from shared.utils.network_utils import format_network_display_name

TRANSACTION_INTENTS = {"transfer", "airtime", "data"}
NON_TRANSACTION_SWITCH_INTENTS = {"query", "account", "faq", "support", "beneficiary"}
KNOWN_SWITCH_INTENTS = TRANSACTION_INTENTS | NON_TRANSACTION_SWITCH_INTENTS
INTERRUPT_REQUIRED_FIELDS_MAX_CHARS = 700
INTERRUPT_PROMPT_MAX_CHARS = 300
INTERRUPT_ACTIVE_TASK_STATE_MAX_CHARS = 700
INTERRUPT_REQUIRED_FIELDS_COMPACT_MAX_CHARS = 240
INTERRUPT_PROMPT_COMPACT_MAX_CHARS = 160
INTERRUPT_ACTIVE_TASK_STATE_COMPACT_MAX_CHARS = 320

def _build_confirmation_reprompt_outbox(
    state: OrchestratorState,
    interrupt: Any,
    task_ids: list[str],
) -> list[dict[str, Any]]:
    if not task_ids:
        return []
    first_task = state.tasks.get(task_ids[0])
    if not first_task:
        return []

    locale = _state_locale(state)
    summary = interrupt.prompt
    if not summary:
        accounts_raw = state.loaded_context.get("accounts") or []
        accounts = [account for account in accounts_raw if isinstance(account, dict)]
        if len(task_ids) == 1:
            summary = build_confirmation_summary(
                task_payload=first_task.payload,
                locale=locale,
                accounts=accounts,
            )
        else:
            parts: list[str] = []
            for task_id in task_ids:
                task = state.tasks.get(task_id)
                if not task:
                    continue
                rendered = build_confirmation_summary(task_payload=task.payload, locale=locale, accounts=accounts)
                if rendered:
                    parts.append(rendered)
            summary = "\n\n".join(parts)
    if not summary:
        return []

    confirmation_payload = first_task.payload.get("confirmation") or {}
    snapshot = confirmation_payload.get("snapshot") or {}
    confirmation_personality_context = None
    if len(task_ids) == 1 and first_task.type == "transfer":
        confirmation_personality_context = transfer_personality_context_from_payload(
            first_task.payload,
            moment="confirmation",
        )
    return [
        {
            "type": "request_confirmation",
            "task_ids": task_ids,
            "header": build_confirmation_header(
                task_types=[state.tasks[task_id].type for task_id in task_ids if task_id in state.tasks],
                locale=locale,
                task_count=len(task_ids),
                task_actions=[
                    str(state.tasks[task_id].payload.get("action") or "")
                    for task_id in task_ids
                    if task_id in state.tasks
                ],
                personality_context=confirmation_personality_context,
            ),
            "summary": summary,
            "snapshot": snapshot,
            "idempotency_key": first_task.payload.get("idempotency_key", "unknown"),
            "actionable_payload": build_actionable_payload_for_tasks(
                [state.tasks[task_id] for task_id in task_ids if task_id in state.tasks]
            ),
        }
    ]

def _auth_header_for_task_ids(state: OrchestratorState, task_ids: list[str], locale: str) -> str:
    task_types = {state.tasks[task_id].type for task_id in task_ids if task_id in state.tasks}
    if len(task_types) == 1:
        return format_auth_reason(next(iter(task_types)), locale=locale)
    return format_auth_reason("mixed", locale=locale)

def _build_auth_reprompt_outbox(state: OrchestratorState, interrupt: Any) -> list[dict[str, Any]]:
    if not interrupt.task_ids:
        return []
    first_task = state.tasks.get(interrupt.task_ids[0])
    if not first_task:
        return []

    locale = _state_locale(state)
    summary = interrupt.prompt or first_task.payload.get("confirmation", {}).get(
        "summary",
        render_message("orchestrator.execution.pin_prompt_default", locale),
    )
    return [
        {
            "type": "auth_request",
            "method": interrupt.auth_method or "pin",
            "task_ids": interrupt.task_ids,
            "idempotency_key": first_task.payload.get("idempotency_key", "unknown"),
            "header": _auth_header_for_task_ids(state, interrupt.task_ids, locale),
            "summary": summary,
            "actionable_payload": build_actionable_payload_for_tasks(
                [state.tasks[task_id] for task_id in interrupt.task_ids if task_id in state.tasks]
            ),
        }
    ]

def _reprompt_updates(state: OrchestratorState, interrupt: Any) -> dict[str, Any]:
    outbox: list[dict[str, Any]] = []
    if interrupt.kind == "input":
        compact_transfer_reprompt = _build_compact_transfer_input_reprompt(state, interrupt)
        if compact_transfer_reprompt:
            logger.info("interrupt_compact_transfer_reprompt", task_ids=interrupt.task_ids)
            outbox = [{"type": "say", "text": compact_transfer_reprompt}]
        elif interrupt.prompt:
            outbox = [{"type": "say", "text": interrupt.prompt}]
    elif interrupt.kind == "confirmation":
        outbox = _build_confirmation_reprompt_outbox(state, interrupt, interrupt.task_ids)
    elif interrupt.kind == "auth":
        outbox = _build_auth_reprompt_outbox(state, interrupt)

    updates: dict[str, Any] = {
        "pending_interrupt": interrupt,
        "last_interrupt": interrupt,
        "tasks": state.tasks,
    }
    if outbox:
        updates["outbox"] = outbox
    return updates

def _build_compact_transfer_input_reprompt(state: OrchestratorState, interrupt: Any) -> str | None:
    if getattr(interrupt, "kind", None) != "input":
        return None
    task_ids = getattr(interrupt, "task_ids", None)
    if not isinstance(task_ids, list) or not task_ids:
        return None

    first_task_id = str(task_ids[0])
    task = state.tasks.get(first_task_id)
    if not task or task.type != "transfer":
        return None

    fields_by_task = getattr(interrupt, "fields_by_task", {}) or {}
    if not isinstance(fields_by_task, dict):
        return None
    required_fields_raw = fields_by_task.get(first_task_id, [])
    required_fields = [field for field in required_fields_raw if isinstance(field, str)]
    required_set = set(required_fields)
    transfer_fields = {"recipient_account", "recipient_bank_name"}
    if not required_set or not required_set.issubset(transfer_fields):
        return None

    locale = _state_locale(state)
    task_payload = task.payload if isinstance(task.payload, dict) else {}
    recipient_label = format_recipient_display_label(
        task_payload.get("recipient_name"),
        task_payload.get("recipient_resolved_name"),
    )
    safe_recipient = sanitize_recipient_display_name(recipient_label, locale)

    if required_set == transfer_fields:
        return cast(
            str,
            render_message(
                "response.templates.ask_account_number_and_bank",
                locale,
                {"recipient_name": safe_recipient},
            ),
        )
    if required_set == {"recipient_account"}:
        return cast(
            str,
            render_message(
                "response.templates.ask_account_number",
                locale,
                {"recipient_name": safe_recipient},
            ),
        )
    if required_set == {"recipient_bank_name"}:
        return cast(str, render_message("response.templates.ask_bank", locale))

    return None

def _format_task_details_for_status(task: TaskSpec, task_type: str) -> str:
    payload = task.payload or {}

    def _fmt_amount(value: Any) -> str | None:
        try:
            return format_naira(float(value))
        except (TypeError, ValueError):
            return None

    if task_type == "transfer":
        recipient = payload.get("recipient_resolved_name") or payload.get("recipient_name")
        amount = payload.get("amount")
        source_bank = payload.get("source_bank_name")
        parts: list[str] = []
        formatted_amount = _fmt_amount(amount)
        if formatted_amount:
            parts.append(f"amount {formatted_amount}")
        if recipient:
            parts.append(f"recipient {recipient}")
        if source_bank:
            parts.append(f"source {source_bank}")
        if parts:
            return "Known details: " + ", ".join(parts) + "."
    if task_type == "airtime":
        phone = payload.get("phone") or payload.get("recipient_phone")
        amount = payload.get("amount")
        network = format_network_display_name(payload.get("network"))
        source_bank = payload.get("source_bank_name")
        parts = []
        formatted_amount = _fmt_amount(amount)
        if formatted_amount:
            parts.append(f"amount {formatted_amount}")
        if network:
            parts.append(f"network {network}")
        if phone:
            parts.append(f"line {phone}")
        if source_bank:
            parts.append(f"source {source_bank}")
        if parts:
            return "Known details: " + ", ".join(parts) + "."
    if task_type == "data":
        phone = payload.get("target_phone") or payload.get("phone") or payload.get("recipient_phone")
        plan = payload.get("plan_name") or payload.get("biller_item_name") or payload.get("plan")
        amount = payload.get("amount")
        network = format_network_display_name(payload.get("network"))
        source_bank = payload.get("source_bank_name")
        parts = []
        if plan:
            parts.append(f"plan {plan}")
        formatted_amount = _fmt_amount(amount)
        if formatted_amount:
            parts.append(f"amount {formatted_amount}")
        if network:
            parts.append(f"network {network}")
        if phone:
            parts.append(f"line {phone}")
        if source_bank:
            parts.append(f"source {source_bank}")
        if parts:
            return "Known details: " + ", ".join(parts) + "."
    return ""

async def _status_query_updates(
    *,
    state: OrchestratorState,
    interrupt: Any,
    route: InterruptRouteDecision,
    current_task_types: set[str],
    semantic_path_shape: str,
    task_planner: Any,
    text: str,
    active_type: str,
    services: dict[str, Any],
    redis_client: Any | None,
) -> dict[str, Any]:
    if not current_task_types or not current_task_types.issubset(TRANSACTION_INTENTS):
        from apps.chat.src.agent.orchestrator.nodes.interrupt.router import _route_interrupt_semantic_turn
        logger.info(
            "interrupt_status_query_no_active_flow",
            kind=interrupt.kind,
            status_query_type=route.status_query_type,
            active_types=sorted(current_task_types),
        )
        semantic_route = await _route_interrupt_semantic_turn(
            task_planner=task_planner,
            state=state,
            text=text,
        )
        semantic_decision = str(getattr(semantic_route, "decision", "") or "")
        expected_executors = [
            str(item)
            for item in (getattr(semantic_route, "expected_transaction_executors", None) or [])
            if str(item) in TRANSACTION_INTENTS
        ]
        logger.info(
            "interrupt_status_query_semantic_recovery",
            decision=semantic_decision or None,
            mode=getattr(semantic_route, "mode", None),
            target_intent=getattr(semantic_route, "target_intent", None),
            expected_executors=expected_executors,
        )

        if semantic_decision == "cancel":
            return await _cancel_updates(state, interrupt, current_task_types, redis_client)

        if semantic_decision in {"planner_mixed", "planner_ambiguous"}:
            return _build_planner_switch_updates(
                state=state,
                interrupt=interrupt,
                active_type=active_type,
                current_task_types=current_task_types,
                text=text,
                expected_executors=expected_executors,
            )

        direct_non_transaction_domains = {
            "domain_query": "query",
            "domain_account": "account",
            "domain_support": "support",
            "domain_beneficiary": "beneficiary",
        }
        target_intent = direct_non_transaction_domains.get(semantic_decision)
        if target_intent is not None:
            recovered_route = InterruptRouteDecision(
                decision="switch_intent",
                confidence=getattr(semantic_route, "confidence", 0.0) or 0.0,
                detected_language=getattr(semantic_route, "detected_language", None),
                target_intent=target_intent,
                target_mode="continuation" if getattr(semantic_route, "mode", None) == "continuation" else "new",
                reason="status_query_no_active_flow_semantic_recovery",
            )
            new_tasks, waves, new_task_types = _build_direct_non_transaction_switch_tasks(
                state=state,
                text=text,
                target_intent=target_intent,
                route=recovered_route,
            )
            return _switch_updates(
                state=state,
                interrupt=interrupt,
                active_type=active_type,
                current_task_types=current_task_types,
                new_tasks=new_tasks,
                waves=waves,
                new_task_types=new_task_types,
                text=text,
                planner_output=None,
                primary_intent=target_intent,
            )

        direct_transaction_domains = {
            "domain_transfer": "transfer",
            "domain_airtime": "airtime",
            "domain_data": "data",
        }
        target_intent = direct_transaction_domains.get(semantic_decision)
        if target_intent is not None:
            new_tasks, waves, new_task_types = await _build_enriched_transaction_switch_tasks(
                state=state,
                text=text,
                target_intent=target_intent,
                interrupt=interrupt,
                services=services,
            )
            return _switch_updates(
                state=state,
                interrupt=interrupt,
                active_type=active_type,
                current_task_types=current_task_types,
                new_tasks=new_tasks,
                waves=waves,
                new_task_types=new_task_types,
                text=text,
                planner_output=None,
                primary_intent=target_intent,
            )

        return _reprompt_updates(state, interrupt)

    response = _build_status_query_response(
        state=state,
        interrupt=interrupt,
        task_types=current_task_types,
        status_query_type=route.status_query_type,
    )
    logger.info(
        "interrupt_status_query_hit",
        kind=interrupt.kind,
        status_query_type=route.status_query_type or "recap",
        active_types=sorted(current_task_types),
    )
    return {
        "pending_interrupt": interrupt,
        "last_interrupt": interrupt,
        "tasks": state.tasks,
        "outbox": [{"type": "say", "text": response}],
        "semantic_path_shape": semantic_path_shape,
    }

def _build_confirmation_scope_clarification_outbox(state: OrchestratorState, interrupt: Any) -> list[dict[str, Any]]:
    del interrupt
    locale = _state_locale(state)
    return [{"type": "say", "text": render_message("transfer.resolve.which_recipient", locale)}]


def _build_status_query_response(
    *,
    state: OrchestratorState,
    interrupt: Any,
    task_types: set[str],
    status_query_type: str | None,
) -> str:
    flow_type = next(iter(sorted(task_types))) if task_types else "transaction"
    first_task = state.tasks.get(interrupt.task_ids[0]) if interrupt.task_ids else None
    required_fields = (
        list((interrupt.fields_by_task or {}).get(interrupt.task_ids[0], [])) if interrupt.task_ids else []
    )
    required_fields = [field for field in required_fields if isinstance(field, str)]
    status_kind = status_query_type or "recap"

    stage_text = {
        "input": "waiting for your input",
        "confirmation": "waiting for your confirmation",
        "auth": "waiting for your authorization",
    }.get(interrupt.kind, "in progress")

    if status_kind == "requirements":
        if required_fields:
            needed = ", ".join(_friendly_required_field(field) for field in required_fields)
            hint = _build_requirements_hint(required_fields, interrupt.kind)
            if hint:
                return f"I still need: {needed}. {hint}".strip()
            return f"I still need: {needed}."
        if interrupt.kind == "confirmation":
            return "I need your confirmation to continue. Reply yes to proceed or no to cancel."
        if interrupt.kind == "auth":
            return "I need PIN authorization to continue."
        return "I am waiting for your next input to continue."

    lines = [f"We are in your {flow_type} flow and currently {stage_text}."]
    if first_task:
        detail_line = _format_task_details_for_status(first_task, flow_type)
        if detail_line:
            lines.append(detail_line)
    if required_fields:
        needed = ", ".join(_friendly_required_field(field) for field in required_fields)
        hint = _build_requirements_hint(required_fields, interrupt.kind)
        if hint:
            lines.append(f"Next step: provide {needed}. {hint}")
        else:
            lines.append(f"Next step: provide {needed}.")
    elif interrupt.kind == "confirmation":
        lines.append("Next step: confirm to continue.")
    elif interrupt.kind == "auth":
        lines.append("Next step: complete authorization.")
    return " ".join(lines)


def _friendly_required_field(field: str) -> str:
    mapping = {
        "recipient_name": "recipient name",
        "recipient_account": "recipient account number",
        "recipient_bank_name": "recipient bank name",
        "recipient_phone": "phone line",
        "target_phone": "phone line",
        "phone": "phone line",
        "network": "mobile network",
        "data_plan_id": "data plan choice",
        "data_plan_preference": "budget or data size",
        "beneficiary_id": "beneficiary selection",
        "source_account_id": "source account selection",
        "amount": "amount",
        "pin": "PIN authorization",
        "confirmation_summary": "confirmation",
    }
    return mapping.get(field, field.replace("_", " "))


def _build_requirements_hint(required_fields: list[str], interrupt_kind: str) -> str:
    hints: list[str] = []
    if "beneficiary_id" in required_fields:
        hints.append("Pick a beneficiary option by tapping it or replying with the number.")
    if "data_plan_id" in required_fields:
        hints.append("Pick a data plan by replying with the option number.")
    if "data_plan_preference" in required_fields:
        hints.append("Reply with a budget or size, like 2k or 5GB.")
    if any(field in required_fields for field in ("recipient_phone", "target_phone", "phone")):
        hints.append("Reply with the phone number, or say my line if it is for you.")
    if "network" in required_fields:
        hints.append("Reply with the network, like MTN, Airtel, Glo, or 9mobile.")
    if "source_account_id" in required_fields:
        hints.append("Pick the source account by tapping it or replying with the number.")
    if "amount" in required_fields:
        hints.append("Reply with the amount (for example 5000).")
    if interrupt_kind == "confirmation":
        hints.append("Reply yes to continue or no to cancel.")
    if interrupt_kind == "auth":
        hints.append("Complete PIN authorization to continue.")
    return " ".join(hints)
