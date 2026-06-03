"""Catalog-grounded data plan selection and plan-query handling."""

from __future__ import annotations

from typing import Any

from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from banking.bills.data.models.types import DataContext, DataGates, DataPayload
from banking.bills.data.pipeline.base import PipelineStep, continue_pipeline
from banking.bills.data.plans.catalog import (
    MAX_PLAN_OPTIONS,
    _apply_early_target_context,
    _apply_inferred_network,
    _apply_plan_payload,
    _bare_purchase_preference_prompt,
    _bare_purchase_required_fields,
    _closest_size,
    _closest_validity,
    _display_network,
    _exact_validity_matches,
    _finish_plan_selection,
    _format_options,
    _format_validity,
    _has_named_recipient,
    _has_plan_preference_signal,
    _infer_usage_intent,
    _matching_size,
    _parse_size_gb,
    _parse_validity_days,
    _plan_choice_params,
    _plan_option,
    _plans_for_payload,
    _rank_plans,
    _selected_candidate_from_reply,
    _top_plan_is_decisive,
)
from banking.presentation.i18n.renderer import render_message


class DataPlanSelectionStep(PipelineStep):
    """Select a concrete provider data plan for purchase flows."""

    def __init__(self, user_message: str | None):
        self.user_message = user_message

    async def run(
        self, payload: DataPayload, context: DataContext, gates: DataGates, worker_context: Any
    ) -> TransactionResult:
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
                return continue_pipeline(payload)
            if _has_named_recipient(payload):
                return continue_pipeline(payload)
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
            return continue_pipeline(payload)
        if not _has_plan_preference_signal(payload, usage_intent):
            return TransactionResult(
                outcome=TransactionOutcome.NEEDS_INPUT,
                required_fields=_bare_purchase_required_fields(payload),
                prompt=_bare_purchase_preference_prompt(payload, locale),
                patch=payload.model_dump(exclude_none=True),
            )
        if not payload.network:
            return continue_pipeline(payload)

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
            candidate_pool = (
                exact_validity_matches or _closest_validity(candidate_pool, validity_days)[:MAX_PLAN_OPTIONS]
            )
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
    ) -> TransactionResult:
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
