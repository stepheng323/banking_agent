"""Catalog helpers for data plan selection and catalog query steps."""

from __future__ import annotations

import re
from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from apps.chat.src.agent.workers.data.models.plans import DataPlan
from apps.chat.src.agent.workers.data.models.types import DataContext, DataPayload
from apps.chat.src.agent.workers.data.plans.service import dedupe_data_plans
from banking.presentation.formatters.currency import format_naira
from banking.presentation.i18n.renderer import render_message
from shared.money import to_naira
from shared.utils.network_utils import (
    format_network_display_name,
    normalize_network_name,
    normalize_nigerian_phone,
    resolve_network_from_phone,
)

MAX_PLAN_OPTIONS = 3
_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(gb|g|mb|m)\b", re.IGNORECASE)
_PLAN_REFERENCE_SELECTION_RE = re.compile(r"^\s*(?:option\s+|number\s+|#)?(\d{1,2})\s*$", re.IGNORECASE)


def _plan_to_payload(plan: DataPlan) -> dict[str, Any]:
    return {
        "plan_code": plan.item_code,
        "plan_name": plan.name,
        "network": plan.network,
        "amount": float(plan.amount),
        "size_gb": plan.size_gb,
        "validity_days": plan.validity_days,
        "biller_code": plan.biller_code,
        "tags": plan.tags,
    }


def _plan_option(plan: DataPlan, index: int) -> dict[str, Any]:
    payload = _plan_to_payload(plan)
    payload["index"] = index
    payload["option_id"] = f"data_plan:{plan.item_code}"
    payload["label"] = _plan_label(plan)
    return payload


def _plan_label(plan: DataPlan) -> str:
    validity = render_message("data.format.validity_days", "en", {"days": plan.validity_days}) if plan.validity_days else ""
    suffix = f" • {validity}" if validity else ""
    return f"{plan.name} — {format_naira(plan.amount)}{suffix}"


def _display_network(network: str | None) -> str:
    return format_network_display_name(network)


def _parse_size_gb(value: str | None) -> float | None:
    if not value:
        return None
    match = _SIZE_RE.search(value.strip())
    if not match:
        return None
    amount = float(match.group(1))
    unit = match.group(2).lower()
    if unit.startswith("m"):
        return amount / 1000
    return amount


def _parse_validity_days(value: str | None) -> int | None:
    if not value:
        return None
    text = value.strip().lower()
    match = re.search(r"(\d{1,3})\s*(?:days?|d)\b", text)
    if match:
        return int(match.group(1))
    if any(word in text for word in ("monthly", "month", "30")):
        return 30
    if any(word in text for word in ("weekly", "week", "7")):
        return 7
    if any(word in text for word in ("daily", "day", "1")):
        return 1
    return None


def _infer_usage_intent(text: str | None) -> str | None:
    if not text:
        return None
    normalized = text.lower()
    if any(token in normalized for token in ("video", "stream", "youtube", "netflix")):
        return "video"
    if any(token in normalized for token in ("social", "whatsapp", "facebook", "instagram", "tiktok")):
        return "social"
    if "night" in normalized or "midnight" in normalized:
        return "night"
    if "weekend" in normalized:
        return "weekend"
    if any(token in normalized for token in ("browse", "browsing", "internet")):
        return "browsing"
    return None


def _plan_tags(plan: DataPlan) -> set[str]:
    tags = {str(tag).strip().lower() for tag in plan.tags if str(tag).strip()}
    text = f"{plan.name} {' '.join(str(value) for value in plan.raw_metadata.values())}".lower()
    if plan.validity_days == 1 or "daily" in text:
        tags.add("daily")
    if plan.validity_days == 7 or "weekly" in text or "week" in text:
        tags.add("weekly")
    if plan.validity_days == 30 or "monthly" in text or "month" in text:
        tags.add("monthly")
    if "night" in text or "midnight" in text:
        tags.add("night")
    if "weekend" in text:
        tags.add("weekend")
    if any(token in text for token in ("social", "whatsapp", "facebook", "instagram", "tiktok")):
        tags.add("social")
    if any(token in text for token in ("youtube", "stream", "video")):
        tags.add("video")
    if "unlimited" in text:
        tags.add("unlimited")
    if "fair" in text and "use" in text:
        tags.add("fair_use")
    return tags


def _usage_tags(usage_intent: str | None) -> set[str]:
    normalized = (usage_intent or "").strip().lower()
    if not normalized:
        return set()
    if normalized in {"video", "video_calls", "streaming"}:
        return {"video"}
    if normalized in {"social", "social_media"}:
        return {"social"}
    if normalized in {"night", "night_plan"}:
        return {"night"}
    if normalized == "weekend":
        return {"weekend"}
    if normalized in {"browsing", "browse", "internet"}:
        return {"browsing"}
    return {normalized}


def _format_validity(plan: DataPlan, locale: str) -> str:
    if not plan.validity_days:
        return render_message("data.plan_selection.validity_unknown", locale)
    return render_message("data.format.validity_days", locale, {"days": plan.validity_days})


def _format_options(plans: list[DataPlan], locale: str) -> str:
    lines: list[str] = []
    for idx, plan in enumerate(plans[:MAX_PLAN_OPTIONS], start=1):
        lines.append(
            render_message(
                "data.plan_selection.option",
                locale,
                {
                    "index": idx,
                    "plan_name": plan.name,
                    "amount": f"{plan.amount:,.0f}",
                    "validity": _format_validity(plan, locale),
                },
            )
        )
    return "\n".join(lines)


def _reply_hint(option_count: int, locale: str) -> str:
    if option_count <= 1:
        return render_message("data.plan_selection.reply_with_one", locale)
    if option_count == 2:
        return render_message("data.plan_selection.reply_with_two", locale)
    return render_message("data.plan_selection.reply_with_range", locale, {"count": option_count})


def _plan_choice_params(plans: list[DataPlan], locale: str, **extra: Any) -> dict[str, Any]:
    return {
        **extra,
        "options": _format_options(plans, locale),
        "reply_hint": _reply_hint(min(len(plans), MAX_PLAN_OPTIONS), locale),
    }


def _rank_plans(
    plans: list[DataPlan],
    *,
    target_validity_days: int | None = None,
    selection_preference: str | None = None,
    usage_intent: str | None = None,
) -> list[DataPlan]:
    requested_tags = _usage_tags(usage_intent)
    has_catalog_usage_match = bool(requested_tags) and any(_plan_tags(plan) & requested_tags for plan in plans)
    preference = (selection_preference or "").strip().lower()

    def key(plan: DataPlan) -> tuple[float, float, float, float, float]:
        size = plan.size_gb or 0.0
        validity = plan.validity_days or 0
        validity_score = -abs(validity - target_validity_days) if target_validity_days and validity else 0.0
        usage_score = 1.0 if has_catalog_usage_match and (_plan_tags(plan) & requested_tags) else 0.0
        amount_score = -float(plan.amount)
        if preference == "cheapest":
            return (usage_score, amount_score, size, validity_score, validity)
        if preference == "longest_validity":
            return (usage_score, validity_score if target_validity_days else validity, size, amount_score, 0.0)
        return (usage_score, size, validity_score, validity, amount_score)

    return sorted(plans, key=key, reverse=True)


def _matching_size(plans: list[DataPlan], size_gb: float) -> list[DataPlan]:
    return [plan for plan in plans if plan.size_gb is not None and abs(plan.size_gb - size_gb) < 0.01]


def _closest_size(plans: list[DataPlan], size_gb: float) -> list[DataPlan]:
    with_sizes = [plan for plan in plans if plan.size_gb is not None]
    return sorted(with_sizes, key=lambda plan: (abs(float(plan.size_gb or 0) - size_gb), plan.amount))


def _closest_validity(plans: list[DataPlan], validity_days: int) -> list[DataPlan]:
    with_validity = [plan for plan in plans if plan.validity_days]
    return sorted(
        with_validity,
        key=lambda plan: (abs(int(plan.validity_days or 0) - validity_days), -(plan.size_gb or 0), plan.amount),
    )


def _exact_validity_matches(plans: list[DataPlan], validity_days: int | None) -> list[DataPlan]:
    if validity_days is None:
        return []
    return [plan for plan in plans if plan.validity_days == validity_days]


def _top_plan_is_decisive(
    top: DataPlan,
    second: DataPlan | None,
    *,
    selection_preference: str | None,
    usage_intent: str | None,
) -> bool:
    if second is None:
        return True
    requested_tags = _usage_tags(usage_intent)
    if requested_tags and (_plan_tags(top) & requested_tags) and not (_plan_tags(second) & requested_tags):
        return True
    preference = (selection_preference or "").strip().lower()
    if preference == "cheapest" and top.amount < second.amount:
        return True
    if preference == "longest_validity" and (top.validity_days or 0) > (second.validity_days or 0):
        return True
    return (top.size_gb or 0) > (second.size_gb or 0)


def _selected_candidate_from_reply(user_message: str | None, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not user_message or not candidates:
        return None
    match = _PLAN_REFERENCE_SELECTION_RE.match(user_message)
    if not match:
        return None
    index = int(match.group(1))
    for candidate in candidates:
        if int(candidate.get("index") or 0) == index:
            return candidate
    return None


def _apply_plan_payload(payload: DataPayload, plan_data: dict[str, Any]) -> None:
    payload.plan_code = str(plan_data.get("plan_code") or "").strip() or None
    payload.plan_name = str(plan_data.get("plan_name") or "").strip() or None
    payload.biller_code = str(plan_data.get("biller_code") or "").strip() or None
    payload.network = str(plan_data.get("network") or payload.network or "").strip().upper() or None
    amount = plan_data.get("amount")
    if amount is not None:
        payload.amount = to_naira(amount)
    size_gb = plan_data.get("size_gb")
    if size_gb is not None:
        payload.plan_size_gb = float(size_gb)
    validity_days = plan_data.get("validity_days")
    if validity_days is not None:
        payload.plan_validity_days = int(validity_days)
    tags = plan_data.get("tags")
    payload.plan_tags = [str(tag) for tag in tags if str(tag).strip()] if isinstance(tags, list) else []
    payload.data_plan_candidates = []
    payload.show_plan_options = False
    payload.data_plan_exclude_codes = []


def _network_matches_phone(network: str | None, phone: str | None) -> bool:
    if not network or not phone:
        return True
    normalized_network = normalize_network_name(network) or str(network).strip().upper()
    inferred_network = resolve_network_from_phone(phone)
    return not inferred_network or inferred_network == normalized_network


def _has_named_recipient(payload: DataPayload) -> bool:
    return bool(
        payload.recipient_name
        or (payload.extraction and payload.extraction.entities.recipient_name)
    )


def _apply_early_target_context(payload: DataPayload, context: DataContext) -> None:
    if payload.target_phone:
        return
    if payload.extraction and payload.extraction.entities.recipient_phone:
        payload.target_phone = payload.extraction.entities.recipient_phone
        payload.is_self = False
        return
    if _has_named_recipient(payload):
        return
    default_phone = normalize_nigerian_phone(context.phone_number) or context.phone_number or None
    if default_phone and _network_matches_phone(payload.network, default_phone):
        payload.target_phone = default_phone
        payload.is_self = True


def _apply_inferred_network(payload: DataPayload) -> None:
    if payload.network or not payload.target_phone:
        return
    inferred_network = resolve_network_from_phone(payload.target_phone)
    if inferred_network:
        payload.network = inferred_network


def _has_plan_preference_signal(payload: DataPayload, usage_intent: str | None) -> bool:
    return any(
        (
            str(payload.plan_code or "").strip(),
            str(payload.plan_name or "").strip(),
            payload.amount is not None,
            str(payload.size_preference or "").strip(),
            str(payload.validity_preference or "").strip(),
            str(payload.selection_preference or "").strip(),
            str(usage_intent or "").strip(),
            payload.data_plan_candidates,
            payload.show_plan_options,
        )
    )


def _bare_purchase_preference_prompt(payload: DataPayload, locale: str) -> str:
    network = _display_network(payload.network)
    if payload.network and payload.is_self:
        return render_message(
            "data.plan_selection.ask_preference_self_network",
            locale,
            {"network": network},
        )
    if payload.network:
        if not payload.target_phone:
            return render_message(
                "data.plan_selection.ask_preference_network_without_phone",
                locale,
                {"network": network},
            )
        return render_message(
            "data.plan_selection.ask_preference_target_network",
            locale,
            {"network": network, "target_phone": payload.target_phone or ""},
        )
    if payload.is_self:
        return render_message("data.plan_selection.ask_preference_self_unknown_network", locale)
    return render_message(
        "data.plan_selection.ask_preference_target_unknown_network",
        locale,
        {"target_phone": payload.target_phone or ""},
    )


def _bare_purchase_required_fields(payload: DataPayload) -> list[str]:
    fields: list[str] = []
    if not payload.target_phone:
        fields.append("target_phone")
    if not payload.network:
        fields.append("network")
    fields.append("data_plan_preference")
    return fields


def _finish_plan_selection(
    payload: DataPayload,
    plan: DataPlan,
    *,
    context: DataContext,
    locale: str,
) -> TransactionResult:
    _apply_plan_payload(payload, _plan_to_payload(plan))
    _apply_early_target_context(payload, context)
    if payload.target_phone:
        return TransactionResult(outcome=TransactionOutcome.OK, patch={})
    if _has_named_recipient(payload):
        return TransactionResult(outcome=TransactionOutcome.OK, patch={})
    return TransactionResult(
        outcome=TransactionOutcome.NEEDS_INPUT,
        required_fields=["target_phone"],
        prompt=render_message(
            "data.plan_selection.recommendation_ask_phone",
            locale,
            {
                "plan_name": plan.name,
                "network": _display_network(payload.network or plan.network),
                "amount": f"{plan.amount:,.0f}",
                "validity": _format_validity(plan, locale),
            },
        ),
        patch=payload.model_dump(exclude_none=True),
    )


async def _plans_for_payload(payload: DataPayload, worker_context: Any) -> list[DataPlan]:
    service = getattr(worker_context, "plan_service", None)
    if not service or not payload.network:
        return []
    return dedupe_data_plans(await service.get_plans(payload.network))
