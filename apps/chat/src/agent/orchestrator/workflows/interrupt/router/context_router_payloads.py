"""Active task payload shaping for interrupt router prompts."""

from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.state_view import interrupt_state_view


def _compact_task_payload_for_interrupt_router(payload: dict[str, Any]) -> dict[str, Any]:
    # Keep only stable routing signals to avoid noisy or sensitive prompt context.
    compact: dict[str, Any] = {}
    scalar_fields = (
        "action",
        "amount",
        "recipient_name",
        "recipient_resolved_name",
        "recipient_phone",
        "target_phone",
        "network",
        "is_self",
        "plan_code",
        "plan_name",
        "plan_size_gb",
        "plan_validity_days",
        "beneficiary_id",
        "source_account_id",
        "source_bank_name",
        "source_account_number",
    )
    for field in scalar_fields:
        value = payload.get(field)
        if isinstance(value, (str, int, float, bool)) and value not in ("", None):
            compact[field] = value

    # Expose destination presence semantically without copying raw account digits into prompt context.
    compact["has_recipient_account"] = bool(payload.get("recipient_account"))
    compact["has_recipient_bank_name"] = bool(payload.get("recipient_bank_name"))

    confirmation = payload.get("confirmation")
    if isinstance(confirmation, dict):
        summary = confirmation.get("summary")
        snapshot = confirmation.get("snapshot")
        confirmation_view: dict[str, Any] = {}
        if isinstance(summary, str) and summary:
            confirmation_view["summary"] = summary
        if isinstance(snapshot, dict):
            confirmation_view["snapshot"] = {
                key: snapshot.get(key)
                for key in (
                    "amount",
                    "recipient_name",
                    "recipient_phone",
                    "target_phone",
                    "network",
                    "is_self",
                    "plan_code",
                    "plan_name",
                    "plan_size_gb",
                    "plan_validity_days",
                    "recipient_account",
                    "recipient_bank_name",
                    "sourceBank",
                    "sourceAccount",
                )
                if key in snapshot and isinstance(snapshot.get(key), (str, int, float, bool))
            }
        if confirmation_view:
            compact["confirmation"] = confirmation_view

    return compact


def _minimal_task_payload_for_interrupt_router(payload: dict[str, Any]) -> dict[str, Any]:
    minimal: dict[str, Any] = {}
    for field in (
        "amount",
        "recipient_name",
        "recipient_resolved_name",
        "recipient_phone",
        "target_phone",
        "network",
        "is_self",
        "plan_name",
        "plan_size_gb",
        "plan_validity_days",
        "source_bank_name",
    ):
        value = payload.get(field)
        if isinstance(value, (str, int, float, bool)) and value not in ("", None):
            minimal[field] = value
    confirmation = payload.get("confirmation")
    if isinstance(confirmation, dict):
        snapshot = confirmation.get("snapshot")
        if isinstance(snapshot, dict):
            summary_view = {
                key: snapshot.get(key)
                for key in (
                    "amount",
                    "recipient_name",
                    "recipient_phone",
                    "target_phone",
                    "network",
                    "is_self",
                    "plan_name",
                    "plan_size_gb",
                    "plan_validity_days",
                    "recipient_bank_name",
                )
                if key in snapshot and isinstance(snapshot.get(key), (str, int, float, bool))
            }
            if summary_view:
                minimal["confirmation"] = summary_view
    if payload.get("recipient_account"):
        minimal["has_recipient_account"] = True
    if payload.get("recipient_bank_name"):
        minimal["has_recipient_bank_name"] = True
    return minimal


def _build_active_task_router_state(
    *,
    state: OrchestratorState,
    task_ids: list[str],
    compact_mode: bool,
) -> dict[str, Any]:
    state_view = interrupt_state_view(state)
    task_state: dict[str, Any] = {}
    for task_id in task_ids:
        task = state_view.task(task_id)
        if not task:
            continue
        payload = cast(dict[str, Any], task.payload)
        task_state[task_id] = {
            "type": str(task.type),
            "stage": str(task.stage),
            "payload": (
                _minimal_task_payload_for_interrupt_router(payload)
                if compact_mode
                else _compact_task_payload_for_interrupt_router(payload)
            ),
        }
    return task_state


__all__ = [
    "_build_active_task_router_state",
    "_compact_task_payload_for_interrupt_router",
    "_minimal_task_payload_for_interrupt_router",
]
