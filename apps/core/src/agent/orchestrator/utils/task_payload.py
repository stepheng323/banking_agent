import re
from typing import Any

from apps.core.src.agent.orchestrator.models.domain import TaskSpec, TaskStage


def apply_source_account_fields(payload: dict[str, Any], plan_item: Any) -> None:
    if plan_item.executor not in ("transfer", "airtime", "data") or not plan_item.parameters:
        return

    if plan_item.parameters.source_bank_name:
        payload["source_bank_name"] = plan_item.parameters.source_bank_name
    if plan_item.parameters.source_account_index is not None:
        payload["source_account_index"] = plan_item.parameters.source_account_index


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _recipient_grounded_in_user_text(recipient: str | None, user_text: str) -> bool:
    """Return True if planner recipient is clearly present in user's original message."""
    norm_recipient = _normalize_text(recipient)
    norm_text = _normalize_text(user_text)
    if not norm_recipient or not norm_text:
        return True
    return norm_recipient in norm_text


def _derive_recipient_from_user_text(planned_recipient: str | None, user_text: str) -> str | None:
    """Derive a safe recipient token from user text when planner over-expands names."""
    norm_planned = _normalize_text(planned_recipient)
    norm_text = _normalize_text(user_text)
    if not norm_text:
        return None

    planned_tokens = [token for token in norm_planned.split() if token]
    text_tokens = {token for token in norm_text.split() if token}
    overlap = [token for token in planned_tokens if token in text_tokens]
    if overlap:
        return overlap[0]

    # Lightweight deterministic fallback: token after a transfer preposition.
    match = re.search(r"\b(?:to|for|si|ga|zuwa)\s+([a-z0-9']+)", norm_text)
    if match:
        candidate = match.group(1).strip()
        return candidate or None

    return None


def _apply_transfer_payload_fields(
    payload: dict[str, Any],
    plan_item: Any,
    fallback_message: str,
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
        recipient_val_str = str(recipient_val) if recipient_val is not None else None
        if not payload.get("recipient_name"):
            if _recipient_grounded_in_user_text(recipient_val_str, fallback_message):
                payload["recipient_name"] = recipient_val
            else:
                derived = _derive_recipient_from_user_text(recipient_val_str, fallback_message)
                if derived:
                    payload["recipient_name"] = derived

    # Guard against planner hallucinating a fully-resolved name not present in user text.
    recipient_name = payload.get("recipient_name")
    if (
        isinstance(recipient_name, str)
        and recipient_name
        and not _recipient_grounded_in_user_text(
            recipient_name,
            fallback_message,
        )
    ):
        derived = _derive_recipient_from_user_text(recipient_name, fallback_message)
        if derived:
            payload["recipient_name"] = derived
        else:
            payload.pop("recipient_name", None)

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
        fallback_message,
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
