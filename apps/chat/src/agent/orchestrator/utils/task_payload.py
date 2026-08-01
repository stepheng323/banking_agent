import re
from typing import Any, cast

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.utils.task_payload_recipients import (
    derive_recipient_from_user_text,
    recipient_account_grounded_in_user_text,
    recipient_bank_grounded_in_user_text,
    recipient_grounded_in_user_text,
    strip_recipient_schedule_suffix,
)
from apps.chat.src.agent.orchestrator.utils.task_payload_schedule import (
    SCHEDULE_MANAGEMENT_ACTIONS,
    derive_schedule_selector_from_user_text,
    derive_transfer_schedule_fields,
    infer_schedule_action_from_text,
)
from apps.chat.src.agent.orchestrator.utils.waves import build_dependency_waves
from banking.runtime.operations import operation_spec
from shared.types.balance import BalanceConversationState, BalanceQueryContract
from shared.types.planner import BaseTaskParameters, dump_task_parameters
from shared.types.read import normalize_read_request
from shared.utils.logging import get_logger
from shared.utils.network_utils import normalize_nigerian_phone
from shared.utils.sanitize import normalize_bank_account_number

_AMOUNT_VALUE_PATTERN = re.compile(r"^\s*(?:₦|ngn)?\s*(\d[\d,]*(?:\.\d+)?)\s*([kKmMhH]?)\s*$")
_BALANCE_SHARE_PERCENT_PATTERN = re.compile(r"\b(\d{1,3})\s*%\b", re.IGNORECASE)
_DATA_PLAN_VALUE_PATTERN = re.compile(r"(?<!\d)(\d{1,3}(?:\.\d+)?)\s*(gb|mb)(?!\w)", re.IGNORECASE)

logger = get_logger(__name__)


def _dump_plan_parameters(parameters: Any) -> dict[str, Any]:
    if not parameters:
        return {}
    if isinstance(parameters, BaseTaskParameters):
        return dump_task_parameters(parameters)

    model_dump = getattr(parameters, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(exclude_none=True)
        except TypeError:
            dumped = model_dump()
        if isinstance(dumped, dict):
            return {key: value for key, value in dumped.items() if value is not None}
        return {}

    if isinstance(parameters, dict):
        return {key: value for key, value in parameters.items() if value is not None}

    return {}


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
    fallback_message: str,
    *,
    strip_recipient_suffix: bool,
    format_narration_requires_recipient_field: bool,
) -> None:
    if plan_item.executor != "transfer":
        return

    action_name = str(payload.get("action") or "")
    if action_name in SCHEDULE_MANAGEMENT_ACTIONS:
        return

    authoritative_fanout_binding = payload.get("recipient_binding_source") == "fanout"

    if plan_item.parameters and plan_item.parameters.reference:
        payload["recipient_reference"] = plan_item.parameters.reference.model_dump(exclude_none=True)

    raw_amount = payload.get("amount")
    if isinstance(raw_amount, str):
        parsed_amount = _parse_amount_value(raw_amount)
        lowered_amount = raw_amount.strip().lower()
        if parsed_amount is not None:
            payload["amount"] = parsed_amount
        elif re.search(r"\b(?:all|everything|max amount|whatever i have)\b", lowered_amount):
            payload["amount"] = None
            payload["transfer_all"] = True
            payload["transfer_percentage"] = None
        else:
            pct_match = _BALANCE_SHARE_PERCENT_PATTERN.search(lowered_amount)
            pct_value: float | None = None
            if pct_match is not None:
                raw_pct = float(pct_match.group(1))
                if 0 < raw_pct <= 100:
                    pct_value = raw_pct
            elif re.search(r"\bhalf\b", lowered_amount):
                pct_value = 50.0
            elif re.search(r"\bquarter\b", lowered_amount):
                pct_value = 25.0
            elif re.search(r"\btithe\b", lowered_amount):
                pct_value = 10.0

            if pct_value is not None:
                payload["amount"] = None
                payload["transfer_percentage"] = pct_value
                payload["transfer_all"] = False
            else:
                payload["amount"] = None

    # Planner schema uses `bank_name`; transfer runtime expects `recipient_bank_name`.
    bank_name = payload.pop("bank_name", None)
    if bank_name and not payload.get("recipient_bank_name"):
        payload["recipient_bank_name"] = bank_name
    payload.pop("recipient_allocations", None)

    if "recipient_account" in payload:
        normalized_account = normalize_bank_account_number(payload.get("recipient_account"))
        if normalized_account:
            payload["recipient_account"] = normalized_account

    has_recipient_field = "recipient" in payload
    if has_recipient_field:
        recipient_val = payload.pop("recipient")
        if strip_recipient_suffix and isinstance(recipient_val, str) and recipient_val:
            recipient_val = recipient_val.rstrip("},. ")
        if isinstance(recipient_val, str) and recipient_val:
            recipient_val = strip_recipient_schedule_suffix(recipient_val)
        recipient_val_str = str(recipient_val) if recipient_val is not None else None
        if not payload.get("recipient_name"):
            if recipient_grounded_in_user_text(recipient_val_str, fallback_message):
                payload["recipient_name"] = recipient_val
            else:
                derived = derive_recipient_from_user_text(recipient_val_str, fallback_message)
                if derived:
                    payload["recipient_name"] = derived
                    logger.info(
                        "transfer_recipient_alias_repair_applied",
                        planner_recipient=recipient_val_str,
                        repaired_recipient=derived,
                    )
                elif authoritative_fanout_binding:
                    payload["recipient_name"] = recipient_val

    # Guard against planner hallucinating a fully-resolved name not present in user text.
    recipient_name = payload.get("recipient_name")
    if isinstance(recipient_name, str) and recipient_name:
        original_recipient_name = recipient_name
        cleaned_recipient_name = strip_recipient_schedule_suffix(recipient_name)
        if cleaned_recipient_name and cleaned_recipient_name != recipient_name:
            payload["recipient_name"] = cleaned_recipient_name
            recipient_name = cleaned_recipient_name
            logger.info(
                "transfer_recipient_schedule_suffix_stripped",
                planner_recipient=original_recipient_name,
                cleaned_recipient=cleaned_recipient_name,
            )
        elif cleaned_recipient_name is None:
            payload.pop("recipient_name", None)
            recipient_name = None
    if (
        isinstance(recipient_name, str)
        and recipient_name
        and not recipient_grounded_in_user_text(
            recipient_name,
            fallback_message,
        )
    ):
        derived = derive_recipient_from_user_text(recipient_name, fallback_message)
        if derived:
            payload["recipient_name"] = derived
            logger.info(
                "transfer_recipient_grounding_repair_applied",
                planner_recipient=recipient_name,
                repaired_recipient=derived,
                authoritative_binding=authoritative_fanout_binding,
            )
        elif not authoritative_fanout_binding:
            logger.info(
                "transfer_recipient_dropped_as_ungrounded",
                planner_recipient=recipient_name,
            )
            payload.pop("recipient_name", None)

    # Guard destination fields against stale planner context leakage.
    recipient_account = payload.get("recipient_account")
    if isinstance(recipient_account, str) and recipient_account:
        if not recipient_account_grounded_in_user_text(recipient_account, fallback_message):
            payload.pop("recipient_account", None)
            payload.pop("recipient_resolved_name", None)
            payload.pop("recipient_bank_code", None)
            payload.pop("recipient_bank_code_provider", None)
            payload.pop("recipient_resolution_provider", None)
            payload.pop("recipient_resolution_mode", None)

    recipient_bank_name = payload.get("recipient_bank_name")
    if isinstance(recipient_bank_name, str) and recipient_bank_name:
        if not recipient_bank_grounded_in_user_text(recipient_bank_name, fallback_message):
            payload.pop("recipient_bank_name", None)
            payload.pop("recipient_bank_code", None)
            payload.pop("recipient_bank_code_provider", None)
            payload.pop("recipient_resolution_provider", None)
            payload.pop("recipient_resolution_mode", None)
            payload.pop("recipient_resolved_name", None)

    if format_narration_requires_recipient_field and not has_recipient_field:
        return

    from shared.utils.narration import format_narration

    payload["narration"] = format_narration(
        payload.get("narration"),
        payload.get("recipient_resolved_name") or payload.get("recipient_name"),
    )

    inferred_schedule_action = infer_schedule_action_from_text(fallback_message)
    if action_name == "send_money" and inferred_schedule_action:
        payload["action"] = inferred_schedule_action
        action_name = inferred_schedule_action

    if action_name in {"schedule_transfer", "recurring_transfer"}:
        schedule_patch = derive_transfer_schedule_fields(
            fallback_message,
            schedule_text=(plan_item.parameters.schedule if plan_item.parameters else None),
            scheduled_text=(plan_item.parameters.scheduled if plan_item.parameters else None),
            recurring_flag=(plan_item.parameters.recurring if plan_item.parameters else None),
        )
        payload.update(schedule_patch)
    elif action_name in SCHEDULE_MANAGEMENT_ACTIONS:
        _apply_schedule_management_payload_fields(payload, plan_item, fallback_message)


def _apply_schedule_management_payload_fields(payload: dict[str, Any], plan_item: Any, fallback_message: str) -> None:
    action_name = str(payload.get("action") or "")
    if action_name not in SCHEDULE_MANAGEMENT_ACTIONS:
        return

    if payload.get("schedule_id") and not payload.get("schedule_selector"):
        payload["schedule_selector"] = payload.get("schedule_id")
    schedule_selector = derive_schedule_selector_from_user_text(fallback_message)
    if schedule_selector:
        payload["schedule_selector"] = schedule_selector
    if action_name == "edit_scheduled_transaction":
        if payload.get("plan") and not payload.get("plan_name"):
            payload["plan_name"] = payload.get("plan")
        if payload.get("phone") and not payload.get("recipient_phone"):
            payload["recipient_phone"] = payload.get("phone")
        payload.update(
            derive_transfer_schedule_fields(
                fallback_message,
                schedule_text=(plan_item.parameters.schedule if plan_item.parameters else None),
                scheduled_text=(plan_item.parameters.scheduled if plan_item.parameters else None),
                recurring_flag=(plan_item.parameters.recurring if plan_item.parameters else None),
            )
        )


def _apply_airtime_payload_fields(payload: dict[str, Any], plan_item: Any, fallback_message: str) -> None:
    if plan_item.executor != "airtime":
        return
    phone = payload.get("phone")
    if isinstance(phone, str) and phone.strip() and not payload.get("recipient_phone"):
        payload["recipient_phone"] = phone
    action_name = str(payload.get("action") or "buy_airtime")
    inferred_schedule_action = infer_schedule_action_from_text(fallback_message)
    if action_name == "buy_airtime" and inferred_schedule_action:
        action_name = "recurring_airtime" if inferred_schedule_action == "recurring_transfer" else "schedule_airtime"
        payload["action"] = action_name
    if action_name in {"schedule_airtime", "recurring_airtime"}:
        payload.update(
            derive_transfer_schedule_fields(
                fallback_message,
                schedule_text=(plan_item.parameters.schedule if plan_item.parameters else None),
                scheduled_text=(plan_item.parameters.scheduled if plan_item.parameters else None),
                recurring_flag=(plan_item.parameters.recurring if plan_item.parameters else None),
            )
        )


def _parse_amount_value(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, float):
        return value
    if isinstance(value, int):
        return float(value)

    text = str(value).strip()
    if not text:
        return None
    match = _AMOUNT_VALUE_PATTERN.match(text)
    if not match:
        return None

    try:
        numeric = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    suffix = (match.group(2) or "").lower()
    multiplier = 1.0
    if suffix == "k":
        multiplier = 1000.0
    elif suffix == "h":
        multiplier = 100.0
    elif suffix == "m":
        multiplier = 1_000_000.0
    amount = numeric * multiplier
    if amount <= 0:
        return None
    return amount


def _data_plan_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = _DATA_PLAN_VALUE_PATTERN.search(value)
    if not match:
        return None
    return f"{match.group(1)}{match.group(2).upper()}"


def _apply_data_payload_fields(payload: dict[str, Any], plan_item: Any, fallback_message: str) -> None:
    if plan_item.executor != "data":
        return

    raw_amount = payload.get("amount")
    if isinstance(raw_amount, str):
        plan_from_amount = _data_plan_value(raw_amount)
        if plan_from_amount:
            payload.setdefault("plan", plan_from_amount)
            payload.pop("amount", None)
            logger.info("data_amount_repaired_to_plan")
        else:
            parsed_amount = _parse_amount_value(raw_amount)
            if parsed_amount is not None:
                payload["amount"] = parsed_amount
            else:
                payload.pop("amount", None)
                logger.info("data_invalid_amount_dropped")

    raw_budget = payload.get("budget")
    if not payload.get("plan") and isinstance(raw_budget, str):
        plan_from_budget = _data_plan_value(raw_budget)
        if plan_from_budget:
            payload["plan"] = plan_from_budget
            logger.info("data_budget_repaired_to_plan")

    target_phone = payload.get("target_phone")
    recipient_phone = payload.get("recipient_phone")
    phone = payload.get("phone")

    if not target_phone and isinstance(recipient_phone, str) and recipient_phone.strip():
        normalized = normalize_nigerian_phone(recipient_phone) or recipient_phone.strip()
        payload["target_phone"] = normalized
    elif not target_phone and isinstance(phone, str) and phone.strip():
        normalized = normalize_nigerian_phone(phone) or phone.strip()
        payload["target_phone"] = normalized

    if (
        "plan" in payload
        and isinstance(payload.get("plan"), str)
        and payload.get("plan")
        and not payload.get("plan_name")
    ):
        payload["plan_name"] = payload.get("plan")

    if payload.get("amount") is None and payload.get("budget") is not None:
        parsed_budget = _parse_amount_value(payload.get("budget"))
        if parsed_budget is not None:
            payload["amount"] = parsed_budget

    action_name = str(payload.get("action") or "buy_data")
    inferred_schedule_action = infer_schedule_action_from_text(fallback_message)
    if action_name == "buy_data" and inferred_schedule_action:
        action_name = "recurring_data" if inferred_schedule_action == "recurring_transfer" else "schedule_data"
        payload["action"] = action_name
    if action_name in {"schedule_data", "recurring_data"}:
        payload.update(
            derive_transfer_schedule_fields(
                fallback_message,
                schedule_text=(plan_item.parameters.schedule if plan_item.parameters else None),
                scheduled_text=(plan_item.parameters.scheduled if plan_item.parameters else None),
                recurring_flag=(plan_item.parameters.recurring if plan_item.parameters else None),
            )
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
    payload = _dump_plan_parameters(plan_item.parameters)
    normalized_read = normalize_read_request(payload)
    if normalized_read is not None:
        payload["read_request"] = normalized_read.model_dump(mode="json", exclude_none=True)
        if normalized_read.subject == "balance":
            raw_balance_contract = payload.get("balance_contract")
            if not isinstance(raw_balance_contract, dict):
                raise ValueError("balance read task is missing balance_contract")
            balance_contract = BalanceQueryContract.model_validate(raw_balance_contract)
            payload["balance_contract"] = balance_contract.model_dump(mode="json", exclude_none=True)
            payload["balance_conversation_state"] = BalanceConversationState(
                focused_bank=(balance_contract.bank_names[0] if len(balance_contract.bank_names) == 1 else None),
                mentioned_banks=balance_contract.bank_names,
                last_result_banks=balance_contract.bank_names,
                last_operation=balance_contract.operation,
            ).model_dump(mode="json", exclude_none=True)

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
        payload["message"] = fallback_message or plan_item.instruction or ""

    source_clause_index = getattr(plan_item, "source_clause_index", None)
    if isinstance(source_clause_index, int) and source_clause_index > 0:
        payload["source_clause_index"] = source_clause_index

    if (
        include_skip_extraction
        and plan_item.executor in ("transfer", "airtime", "data")
        and str(payload.get("action") or "") not in SCHEDULE_MANAGEMENT_ACTIONS
    ):
        payload["skip_extraction"] = True

    _apply_transfer_payload_fields(
        payload,
        plan_item,
        fallback_message,
        strip_recipient_suffix=strip_transfer_recipient_suffix,
        format_narration_requires_recipient_field=format_narration_requires_recipient_field,
    )
    _apply_airtime_payload_fields(payload, plan_item, fallback_message)
    _apply_data_payload_fields(payload, plan_item, fallback_message)
    _apply_schedule_management_payload_fields(payload, plan_item, fallback_message)
    apply_source_account_fields(payload, plan_item)

    task_type = "schedule" if str(payload.get("action") or "") in SCHEDULE_MANAGEMENT_ACTIONS else plan_item.executor
    operation = operation_spec(str(task_type), str(payload.get("action") or ""))
    declared_risk = str(getattr(plan_item, "risk", "") or "")
    if declared_risk and declared_risk != operation.risk:
        raise ValueError(
            f"Planner risk {declared_risk!r} does not match operation {operation.action!r} risk {operation.risk!r}"
        )
    operation.parameter_model.model_validate(_dump_plan_parameters(plan_item.parameters))
    logger.info(
        "worker_operation_materialized",
        domain=operation.domain,
        executor=operation.executor,
        canonical_action=operation.action,
        risk=operation.risk,
        requires_confirmation=operation.requires_confirmation,
        requires_pin=operation.requires_pin,
    )

    return TaskSpec(
        id=plan_item.task_id,
        type=cast(Any, task_type),
        depends_on=list(plan_item.depends_on or []),
        stage=TaskStage.DRAFT,
        payload=payload,
    )


def build_task_specs_from_plan_items(
    plan_items: list[Any],
    fallback_message: str,
    *,
    preserve_existing_action_instruction: bool,
    include_skip_extraction: bool,
    strip_transfer_recipient_suffix: bool,
    format_narration_requires_recipient_field: bool,
    payload_overrides_by_task_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, TaskSpec]:
    task_specs: dict[str, TaskSpec] = {}
    for plan_item in plan_items:
        spec = build_task_spec_from_plan_item(
            plan_item,
            fallback_message,
            preserve_existing_action_instruction=preserve_existing_action_instruction,
            include_skip_extraction=include_skip_extraction,
            strip_transfer_recipient_suffix=strip_transfer_recipient_suffix,
            format_narration_requires_recipient_field=format_narration_requires_recipient_field,
        )
        payload_override = (payload_overrides_by_task_id or {}).get(spec.id)
        if payload_override:
            spec.payload.update(payload_override)
        task_specs[spec.id] = spec
    return task_specs


def build_task_specs_and_waves_from_plan_items(
    plan_items: list[Any],
    fallback_message: str,
    *,
    preserve_existing_action_instruction: bool,
    include_skip_extraction: bool,
    strip_transfer_recipient_suffix: bool,
    format_narration_requires_recipient_field: bool,
    payload_overrides_by_task_id: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, TaskSpec], list[list[str]]]:
    task_specs = build_task_specs_from_plan_items(
        plan_items,
        fallback_message,
        preserve_existing_action_instruction=preserve_existing_action_instruction,
        include_skip_extraction=include_skip_extraction,
        strip_transfer_recipient_suffix=strip_transfer_recipient_suffix,
        format_narration_requires_recipient_field=format_narration_requires_recipient_field,
        payload_overrides_by_task_id=payload_overrides_by_task_id,
    )
    task_ids = list(task_specs.keys())
    depends_on_by_task = {task_id: list(spec.depends_on) for task_id, spec in task_specs.items()}
    waves = build_dependency_waves(task_ids, depends_on_by_task)
    return task_specs, waves
