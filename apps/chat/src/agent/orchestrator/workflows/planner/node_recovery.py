"""Planner runner recovery helpers for empty or misrouted planner output."""

from typing import Any, Literal

from banking.intent.routing_signals import looks_like_transaction_replay_modifier_request
from shared.types.planner import PlannedTask, TaskParameters
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_UNEXPECTED_ROUTE_RECOVERY_MIN_CONFIDENCE = 0.5
_PLANNER_TRANSFER_PREFIXES = ("send", "transfer", "pay", "remit")


def _recover_unexpected_question_task(planner_output: Any, text: str) -> PlannedTask | None:
    if not planner_output or getattr(planner_output, "tasks", None):
        return None

    primary_intent = str(getattr(planner_output, "primary_intent", "") or "").strip().lower()
    if primary_intent not in {"faq", "support"}:
        return None

    confidence = float(getattr(planner_output, "confidence", 0.0) or 0.0)
    if confidence < _UNEXPECTED_ROUTE_RECOVERY_MIN_CONFIDENCE:
        return None

    instruction = str(getattr(planner_output, "normalized_instruction", "") or "").strip() or text
    if not instruction:
        return None

    executor: Literal["faq", "support"] = "faq" if primary_intent == "faq" else "support"
    action = "answer_question" if executor == "faq" else "handle_request"
    return PlannedTask(
        task_id=f"{executor}_unexpected_question",
        action=action,
        executor=executor,
        instruction=instruction,
        parameters=TaskParameters(),
        risk="READ_ONLY",
    )


def _looks_like_amount_only_transfer_start(text: str) -> bool:
    normalized = " ".join((text or "").strip().lower().split())
    if not normalized:
        return False
    if not any(normalized.startswith(prefix) for prefix in _PLANNER_TRANSFER_PREFIXES):
        return False
    if not any(char.isdigit() for char in normalized):
        return False
    if any(marker in normalized for marker in (" and ", " then ", ",")):
        return False
    if any(keyword in normalized for keyword in ("airtime", "data", "bundle", "balance", "transaction", "statement")):
        return False
    return True


def _recover_missing_slot_transfer_task(planner_output: Any, text: str) -> PlannedTask | None:
    if not planner_output or getattr(planner_output, "tasks", None):
        return None
    if not _looks_like_amount_only_transfer_start(text):
        return None

    primary_intent = str(getattr(planner_output, "primary_intent", "") or "").strip().lower()
    if primary_intent not in {"conversational", "transfer"}:
        return None

    return PlannedTask(
        task_id="transfer_missing_slots_recovery",
        action="send_money",
        executor="transfer",
        instruction=text,
        parameters=TaskParameters(),
        risk="MONEY_MOVE",
    )


def _recover_replay_modifier_transfer_task(planner_output: Any, text: str) -> PlannedTask | None:
    if not looks_like_transaction_replay_modifier_request(text):
        return None
    if not planner_output:
        return None

    tasks = list(getattr(planner_output, "tasks", None) or [])
    primary_intent = str(getattr(planner_output, "primary_intent", "") or "").strip().lower()
    if tasks:
        non_support_tasks = [task for task in tasks if getattr(task, "executor", None) != "support"]
        if non_support_tasks:
            return None
    elif primary_intent not in {"support", "conversational"}:
        return None

    return PlannedTask(
        task_id="transfer_replay_modifier_recovery",
        action="send_money",
        executor="transfer",
        instruction=text,
        parameters=TaskParameters(),
        risk="MONEY_MOVE",
    )


def _apply_planner_recovery(planner_output: Any, text: str, *, active_session_present: bool) -> bool:
    recovered_replay_task = _recover_replay_modifier_transfer_task(planner_output, text)
    if recovered_replay_task is not None:
        planner_output.tasks = [recovered_replay_task]
        planner_output.primary_intent = "transfer"
        planner_output.response = ""
        planner_output.response_key = None
        logger.info(
            "unexpected_turn_route_breadcrumb",
            user_turn_kind=str(getattr(planner_output, "primary_intent", "unknown") or "unknown"),
            active_session_present=active_session_present,
            selected_route="transfer_replay_modifier_recovery",
            route_reason="planner_support_replay_modifier_recovery",
            policy_blocked=False,
            fallback_path="worker_task_injected",
        )
        return True

    recovered_task = _recover_unexpected_question_task(planner_output, text)
    if recovered_task is not None:
        planner_output.tasks = [recovered_task]
        planner_output.response = ""
        planner_output.response_key = None
        logger.info(
            "unexpected_turn_route_breadcrumb",
            user_turn_kind=planner_output.primary_intent,
            active_session_present=active_session_present,
            selected_route=f"{planner_output.primary_intent}_task",
            route_reason="planner_primary_intent_no_task_recovery",
            policy_blocked=False,
            fallback_path="worker_task_injected",
        )
        return True

    recovered_transfer_task = _recover_missing_slot_transfer_task(planner_output, text)
    if recovered_transfer_task is None:
        return False

    planner_output.tasks = [recovered_transfer_task]
    planner_output.primary_intent = "transfer"
    planner_output.response = ""
    planner_output.response_key = None
    logger.info(
        "unexpected_turn_route_breadcrumb",
        user_turn_kind=str(getattr(planner_output, "primary_intent", "unknown") or "unknown"),
        active_session_present=active_session_present,
        selected_route="transfer_missing_slot_recovery",
        route_reason="planner_amount_only_transfer_recovery",
        policy_blocked=False,
        fallback_path="worker_task_injected",
    )
    return True


__all__ = ["_apply_planner_recovery"]
