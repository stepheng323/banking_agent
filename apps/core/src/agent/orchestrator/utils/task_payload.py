from typing import Any

from apps.core.src.agent.orchestrator.models.domain import TaskSpec, TaskStage


def apply_source_account_fields(payload: dict[str, Any], plan_item: Any) -> None:
    if plan_item.executor not in ("transfer", "airtime", "data") or not plan_item.parameters:
        return

    if plan_item.parameters.source_bank_name:
        payload["source_bank_name"] = plan_item.parameters.source_bank_name
    if plan_item.parameters.source_account_index is not None:
        payload["source_account_index"] = plan_item.parameters.source_account_index


def _apply_transfer_payload_fields(
    payload: dict[str, Any],
    plan_item: Any,
    *,
    strip_recipient_suffix: bool,
    format_narration_requires_recipient_field: bool,
) -> None:
    if plan_item.executor != "transfer":
        return

    has_recipient_field = "recipient" in payload
    if has_recipient_field:
        recipient_val = payload.pop("recipient")
        if strip_recipient_suffix and isinstance(recipient_val, str) and recipient_val:
            recipient_val = recipient_val.rstrip("},. ")
        if not payload.get("recipient_name"):
            payload["recipient_name"] = recipient_val

    if format_narration_requires_recipient_field and not has_recipient_field:
        return

    from shared.utils.narration import format_narration

    payload["narration"] = format_narration(
        payload.get("narration"),
        payload.get("recipient_resolved_name") or payload.get("recipient_name"),
    )


def build_task_spec_from_plan_item(
    plan_item: Any,
    fallback_message: str,
    *,
    preserve_existing_action_instruction: bool,
    include_skip_extraction: bool,
    strip_transfer_recipient_suffix: bool,
    format_narration_requires_recipient_field: bool,
) -> TaskSpec:
    payload = plan_item.parameters.model_dump() if plan_item.parameters else {}

    if plan_item.action:
        if preserve_existing_action_instruction:
            payload.setdefault("action", plan_item.action)
        else:
            payload["action"] = plan_item.action

    if plan_item.instruction:
        if preserve_existing_action_instruction:
            payload.setdefault("instruction", plan_item.instruction)
        else:
            payload["instruction"] = plan_item.instruction

    if plan_item.executor == "query" and not payload.get("message"):
        payload["message"] = plan_item.instruction or fallback_message

    if include_skip_extraction and plan_item.executor in ("transfer", "airtime", "data"):
        payload["skip_extraction"] = True

    _apply_transfer_payload_fields(
        payload,
        plan_item,
        strip_recipient_suffix=strip_transfer_recipient_suffix,
        format_narration_requires_recipient_field=format_narration_requires_recipient_field,
    )
    apply_source_account_fields(payload, plan_item)

    return TaskSpec(
        id=plan_item.task_id,
        type=plan_item.executor,
        depends_on=list(plan_item.depends_on or []),
        stage=TaskStage.DRAFT,
        payload=payload,
    )
