"""Catalog-grounded data plan selection and plan-query handling."""

from __future__ import annotations

import re
from typing import Any

from apps.chat.src.agent.graphs.data.models import DataPlan
from apps.chat.src.agent.graphs.data.models.types import DataContext, DataGates, DataPayload
from apps.chat.src.agent.graphs.data.pipeline.base import PipelineStep
from apps.chat.src.agent.graphs.data.plan_service import dedupe_data_plans
from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.formatters.currency import format_naira
from shared.i18n import render_message
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
        payload.amount = float(amount)
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
) -> TransactionResult | None:
    _apply_plan_payload(payload, _plan_to_payload(plan))
    _apply_early_target_context(payload, context)
    if payload.target_phone:
        return None
    if _has_named_recipient(payload):
        return None
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


class DataPlanSelectionStep(PipelineStep):
    """Select a concrete provider data plan for purchase flows."""

    def __init__(self, user_message: str | None):
        self.user_message = user_message

    async def run(
        self, payload: DataPayload, context: DataContext, gates: DataGates, worker_context: Any
    ) -> TransactionResult | None:
        del gates
        locale = context.language
        _apply_early_target_context(payload, context)
        _apply_inferred_network(payload)
        usage_intent = payload.usage_intent or _infer_usage_intent(self.user_message)
        selected = _selected_candidate_from_reply(self.user_message, payload.data_plan_candidates)
        if selected:
            _apply_plan_payload(payload, selected)
            _apply_early_target_context(payload, context)
            if payload.target_phone:
                return None
            if _has_named_recipient(payload):
                return None
            validity_days = selected.get("validity_days")
            validity = (
                render_message("data.format.validity_days", locale, {"days": validity_days})
                if validity_days
                else render_message("data.plan_selection.validity_unknown", locale)
            )
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["target_phone"],
                prompt=render_message(
                    "data.plan_selection.recommendation_ask_phone",
                    locale,
                    {
                        "plan_name": payload.plan_name or selected.get("label") or "",
                        "network": _display_network(payload.network or selected.get("network")),
                        "amount": f"{float(payload.amount or 0):,.0f}",
                        "validity": validity,
                    },
                ),
                patch=payload.model_dump(exclude_none=True),
            )

        if payload.plan_code and payload.plan_name and payload.amount and payload.target_phone:
            return None
        if not _has_plan_preference_signal(payload, usage_intent):
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=_bare_purchase_required_fields(payload),
                prompt=_bare_purchase_preference_prompt(payload, locale),
                patch=payload.model_dump(exclude_none=True),
            )
        if not payload.network:
            return None

        plans = await _plans_for_payload(payload, worker_context)
        if not plans:
            return TransactionResult(
                outcome=TransactionOutcome.FAILED,
                error=render_message("data.plan_selection.catalog_unavailable", locale),
                response=render_message("data.plan_selection.catalog_unavailable", locale),
                patch=payload.model_dump(exclude_none=True),
            )

        plan_code = str(payload.plan_code or "").strip()
        if plan_code:
            match = next((plan for plan in plans if plan.item_code == plan_code), None)
            if match:
                return _finish_plan_selection(payload, match, context=context, locale=locale)
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["data_plan_id"],
                prompt=render_message("data.plan_selection.invalid_plan_code", locale),
                patch=payload.model_dump(exclude_none=True),
            )

        size_gb = _parse_size_gb(payload.size_preference or payload.plan_name)
        validity_days = _parse_validity_days(payload.validity_preference)
        budget = float(payload.amount) if payload.amount else None
        excluded_codes = {str(code).strip() for code in payload.data_plan_exclude_codes if str(code).strip()}
        visible_plans = [plan for plan in plans if plan.item_code not in excluded_codes] or plans

        if payload.show_plan_options:
            candidate_pool = visible_plans
            if budget is not None:
                candidate_pool = [plan for plan in candidate_pool if float(plan.amount) <= budget]
                if not candidate_pool:
                    return TransactionResult(
                        outcome=TransactionOutcome.NEEDS_INPUT,
                        required_fields=["data_plan_id"],
                        prompt=render_message(
                            "data.plan_selection.no_budget_match",
                            locale,
                            {"network": _display_network(payload.network), "budget": f"{budget:,.0f}"},
                        ),
                        patch=payload.model_dump(exclude_none=True),
                    )
            if size_gb is not None:
                candidate_pool = _matching_size(candidate_pool, size_gb) or _closest_size(candidate_pool, size_gb)
            if validity_days is not None:
                exact_validity_matches = [plan for plan in candidate_pool if plan.validity_days == validity_days]
                candidate_pool = exact_validity_matches or _closest_validity(candidate_pool, validity_days)
            options = _rank_plans(
                candidate_pool,
                target_validity_days=validity_days,
                selection_preference=payload.selection_preference,
                usage_intent=usage_intent,
            )[:MAX_PLAN_OPTIONS]
            if not options:
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["data_plan_id"],
                    prompt=render_message("data.plan_selection.invalid_plan_code", locale),
                    patch=payload.model_dump(exclude_none=True),
                )
            payload.data_plan_candidates = [_plan_option(plan, idx) for idx, plan in enumerate(options, start=1)]
            payload.show_plan_options = False
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["data_plan_id"],
                prompt=render_message(
                    "data.plan_selection.choose_plan",
                    locale,
                    _plan_choice_params(options, locale),
                ),
                patch=payload.model_dump(exclude_none=True),
            )

        if size_gb is not None:
            exact_size_matches = _matching_size(visible_plans, size_gb)
            affordable_size_matches = [
                plan for plan in exact_size_matches if budget is None or float(plan.amount) <= budget
            ]
            if not affordable_size_matches and exact_size_matches and budget is not None:
                cheapest = min(exact_size_matches, key=lambda plan: plan.amount)
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["data_plan_id"],
                    prompt=render_message(
                        "data.plan_selection.size_budget_conflict",
                        locale,
                        {
                            "size": payload.size_preference or f"{size_gb:g}GB",
                            "budget": f"{budget:,.0f}",
                            "amount": f"{cheapest.amount:,.0f}",
                        },
                    ),
                    patch=payload.model_dump(exclude_none=True),
                )
            if len(affordable_size_matches) == 1:
                return _finish_plan_selection(payload, affordable_size_matches[0], context=context, locale=locale)
            if affordable_size_matches:
                exact_validity_size_matches = _exact_validity_matches(affordable_size_matches, validity_days)
                if len(exact_validity_size_matches) == 1:
                    return _finish_plan_selection(
                        payload,
                        exact_validity_size_matches[0],
                        context=context,
                        locale=locale,
                    )
                if exact_validity_size_matches:
                    affordable_size_matches = exact_validity_size_matches
                ranked = _rank_plans(
                    affordable_size_matches,
                    target_validity_days=validity_days,
                    selection_preference=payload.selection_preference,
                    usage_intent=usage_intent,
                )
                options = ranked[:MAX_PLAN_OPTIONS]
                payload.data_plan_candidates = [_plan_option(plan, idx) for idx, plan in enumerate(options, start=1)]
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["data_plan_id"],
                    prompt=render_message(
                        "data.plan_selection.choose_plan",
                        locale,
                        _plan_choice_params(options, locale),
                    ),
                    patch=payload.model_dump(exclude_none=True),
                )

        candidate_pool = visible_plans
        if budget is not None:
            candidate_pool = [plan for plan in visible_plans if float(plan.amount) <= budget]
            if not candidate_pool:
                return TransactionResult(
                    outcome=TransactionOutcome.NEEDS_INPUT,
                    required_fields=["data_plan_id"],
                    prompt=render_message(
                        "data.plan_selection.no_budget_match",
                        locale,
                        {"network": _display_network(payload.network), "budget": f"{budget:,.0f}"},
                    ),
                    patch=payload.model_dump(exclude_none=True),
                )

        if validity_days is not None and budget is None and size_gb is None:
            exact_validity_matches = [plan for plan in candidate_pool if plan.validity_days == validity_days]
            candidate_pool = exact_validity_matches or _closest_validity(candidate_pool, validity_days)[:MAX_PLAN_OPTIONS]
            if len(candidate_pool) == 1:
                return _finish_plan_selection(payload, candidate_pool[0], context=context, locale=locale)
            payload.data_plan_candidates = [_plan_option(plan, idx) for idx, plan in enumerate(candidate_pool, start=1)]
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["data_plan_id"],
                prompt=render_message(
                    "data.plan_selection.choose_plan",
                    locale,
                    _plan_choice_params(candidate_pool, locale),
                ),
                patch=payload.model_dump(exclude_none=True),
            )

        if budget is not None:
            ranked = _rank_plans(
                candidate_pool,
                target_validity_days=validity_days,
                selection_preference=payload.selection_preference,
                usage_intent=usage_intent,
            )
            top = ranked[0]
            second = ranked[1] if len(ranked) > 1 else None
            if _top_plan_is_decisive(
                top,
                second,
                selection_preference=payload.selection_preference,
                usage_intent=usage_intent,
            ):
                return _finish_plan_selection(payload, top, context=context, locale=locale)
            options = ranked[:MAX_PLAN_OPTIONS]
            payload.data_plan_candidates = [_plan_option(plan, idx) for idx, plan in enumerate(options, start=1)]
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["data_plan_id"],
                prompt=render_message(
                    "data.plan_selection.choose_plan",
                    locale,
                    _plan_choice_params(options, locale),
                ),
                patch=payload.model_dump(exclude_none=True),
            )

        options = _rank_plans(
            visible_plans,
            target_validity_days=validity_days,
            selection_preference=payload.selection_preference,
            usage_intent=usage_intent,
        )[:MAX_PLAN_OPTIONS]
        payload.data_plan_candidates = [_plan_option(plan, idx) for idx, plan in enumerate(options, start=1)]
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_INPUT,
            required_fields=["data_plan_id"],
            prompt=render_message(
                "data.plan_selection.ask_preference",
                locale,
                _plan_choice_params(options, locale, network=_display_network(payload.network)),
            ),
            patch=payload.model_dump(exclude_none=True),
        )


class DataPlanQueryStep(PipelineStep):
    """Answer catalog questions without starting a purchase."""

    async def run(
        self, payload: DataPayload, context: DataContext, gates: DataGates, worker_context: Any
    ) -> TransactionResult | None:
        del gates
        locale = context.language
        if not payload.network:
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=["network"],
                prompt=render_message("data.plan_query.ask_network", locale),
                patch=payload.model_dump(exclude_none=True),
            )

        plans = await _plans_for_payload(payload, worker_context)
        if not plans:
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                response=render_message("data.plan_selection.catalog_unavailable", locale),
                patch=payload.model_dump(exclude_none=True),
            )

        size_gb = _parse_size_gb(payload.size_preference or payload.plan_name)
        validity_days = _parse_validity_days(payload.validity_preference)
        budget = float(payload.amount) if payload.amount else None
        usage_intent = payload.usage_intent

        if size_gb is not None:
            exact = _matching_size(plans, size_gb)
            if exact:
                ranked = _rank_plans(
                    exact,
                    target_validity_days=validity_days,
                    selection_preference=payload.selection_preference,
                    usage_intent=usage_intent,
                )
                exact_validity_matches = _exact_validity_matches(ranked, validity_days)
                if len(exact_validity_matches) == 1:
                    ranked = exact_validity_matches
                if len(ranked) == 1:
                    plan = ranked[0]
                    return TransactionResult(
                        outcome=TransactionOutcome.OK,
                        response=render_message(
                            "data.plan_query.exact_match",
                            locale,
                            {
                                "network": _display_network(payload.network or plan.network),
                                "plan_name": plan.name,
                                "amount": f"{plan.amount:,.0f}",
                                "validity": _format_validity(plan, locale),
                            },
                        ),
                        patch={
                            **payload.model_dump(exclude_none=True),
                            "data_plan_query_results": [_plan_option(plan, 1)],
                        },
                    )
                options = ranked[:MAX_PLAN_OPTIONS]
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    response=render_message(
                        "data.plan_query.multiple_matches",
                        locale,
                        {"options": _format_options(options, locale)},
                    ),
                    patch={
                        **payload.model_dump(exclude_none=True),
                        "data_plan_query_results": [
                            _plan_option(plan, idx) for idx, plan in enumerate(options, start=1)
                        ],
                    },
                )
            nearest = _closest_size(plans, size_gb)[:MAX_PLAN_OPTIONS]
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                response=render_message(
                    "data.plan_query.nearest_matches",
                    locale,
                    {"options": _format_options(nearest, locale)},
                ),
                patch={
                    **payload.model_dump(exclude_none=True),
                    "data_plan_query_results": [_plan_option(plan, idx) for idx, plan in enumerate(nearest, start=1)],
                },
            )

        if budget is not None:
            options = _rank_plans(
                [plan for plan in plans if plan.amount <= budget],
                target_validity_days=validity_days,
                selection_preference=payload.selection_preference,
                usage_intent=usage_intent,
            )[:MAX_PLAN_OPTIONS]
            if not options:
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    response=render_message(
                        "data.plan_selection.no_budget_match",
                        locale,
                        {"network": _display_network(payload.network), "budget": f"{budget:,.0f}"},
                    ),
                    patch=payload.model_dump(exclude_none=True),
                )
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                response=render_message(
                    "data.plan_query.budget_matches",
                    locale,
                    {
                        "network": _display_network(payload.network),
                        "budget": f"{budget:,.0f}",
                        "options": _format_options(options, locale),
                    },
                ),
                patch={
                    **payload.model_dump(exclude_none=True),
                    "data_plan_query_results": [_plan_option(plan, idx) for idx, plan in enumerate(options, start=1)],
                },
            )

        if validity_days is not None:
            options = _closest_validity(plans, validity_days)[:MAX_PLAN_OPTIONS]
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                response=render_message(
                    "data.plan_query.validity_matches",
                    locale,
                    {"options": _format_options(options, locale)},
                ),
                patch={
                    **payload.model_dump(exclude_none=True),
                    "data_plan_query_results": [_plan_option(plan, idx) for idx, plan in enumerate(options, start=1)],
                },
            )

        options = _rank_plans(
            plans,
            selection_preference=payload.selection_preference,
            usage_intent=usage_intent,
        )[:MAX_PLAN_OPTIONS]
        return TransactionResult(
            outcome=TransactionOutcome.OK,
            response=render_message(
                "data.plan_query.generic_matches",
                locale,
                {"network": _display_network(payload.network), "options": _format_options(options, locale)},
            ),
            patch={
                **payload.model_dump(exclude_none=True),
                "data_plan_query_results": [_plan_option(plan, idx) for idx, plan in enumerate(options, start=1)],
            },
        )
