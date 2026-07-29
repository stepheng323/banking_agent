"""Deterministic variance-drivers presentation and surface construction."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

from banking.presentation.formatters.currency import format_naira
from banking.presentation.i18n.message_keys import MessageKey
from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.contracts import (
    SelectionPayload,
    SurfaceItemView,
    SurfaceView,
    SurfaceViewMode,
    VarianceDriversEvidenceSelection,
)
from banking.transactions.query.services.analysis.kernel.contracts import (
    Dimension,
    InsightEvidenceSelector,
    PeriodComparison,
    VarianceAnalysisResult,
    VarianceDriver,
)


def _format_relative_change(change: Decimal, pct: Decimal | None, language: str) -> str:
    if change == 0:
        return render_message("query.insight.change_unchanged", language)
    if pct is None:
        if change > 0:
            return render_message("query.insight.change_new", language)
        return render_message("query.insight.change_ended", language)
    pct_str = f"{abs(pct) * 100:.0f}%"
    if change > 0:
        return render_message("query.insight.change_increased", language, {"pct": pct_str})
    return render_message("query.insight.change_decreased", language, {"pct": pct_str})


def _render_driver(driver: VarianceDriver, language: str) -> str:
    label = driver.label or driver.key
    direction_key = cast(
        MessageKey,
        "query.insight.increased" if driver.absolute_delta > 0 else "query.insight.decreased",
    )
    change_text = _format_relative_change(driver.absolute_delta, driver.relative_delta, language)
    return render_message(
        "query.insight.driver",
        language,
        {
            "label": label,
            "direction": render_message(direction_key, language),
            "amount": format_naira(abs(driver.absolute_delta)),
            "change": change_text,
        },
    )


def _render_metric_headline(comparison: PeriodComparison, language: str) -> str:
    measure_key = cast(MessageKey, f"query.insight.measure.{comparison.metric.value}")
    measure_label = render_message(measure_key, language)
    change_text = _format_relative_change(comparison.absolute_delta, comparison.relative_delta, language)

    if comparison.absolute_delta == 0:
        return render_message(
            "query.insight.metric_summary_no_change",
            language,
            {
                "measure": measure_label,
                "current": format_naira(comparison.current_value),
                "baseline": format_naira(comparison.baseline_value),
            },
        )

    direction_key = cast(
        MessageKey,
        "query.insight.increased" if comparison.absolute_delta > 0 else "query.insight.decreased",
    )
    return render_message(
        "query.insight.metric_summary",
        language,
        {
            "measure": measure_label,
            "current": format_naira(comparison.current_value),
            "baseline": format_naira(comparison.baseline_value),
            "change": format_naira(abs(comparison.absolute_delta)),
            "direction": render_message(direction_key, language),
            "relative_change": change_text,
        },
    )


def format_variance_result(result: VarianceAnalysisResult, language: str = "en") -> str:
    """Render a variance analysis result into user-facing text."""
    if not result.available:
        return render_message("query.insight.coverage_required", language)

    lines: list[str] = []

    if result.measure == "cash_flow_overview":
        lines.append(render_message("query.insight.overview_headline", language))
        for comparison in result.metric_comparisons:
            lines.append(_render_metric_headline(comparison, language))
    else:
        if result.metric_comparisons:
            lines.append(_render_metric_headline(result.metric_comparisons[0], language))

    for view in result.dimension_views:
        if not view.drivers:
            continue
        heading_key = cast(MessageKey, f"query.insight.dimension.{view.dimension.value}")
        dimension_label = render_message(heading_key, language)
        lines.append("")
        if result.measure == "cash_flow_overview":
            measure_label = render_message(
                cast(MessageKey, f"query.insight.measure.{view.metric.value}"),
                language,
            )
            lines.append(
                render_message(
                    "query.insight.dimension_measure_heading",
                    language,
                    {"measure": measure_label, "dimension": dimension_label},
                )
            )
        else:
            lines.append(render_message("query.insight.dimension_heading", language, {"dimension": dimension_label}))
        for driver in view.drivers:
            lines.append(_render_driver(driver, language))

    if result.residual != 0:
        lines.append("")
        lines.append(
            render_message(
                "query.insight.residual",
                language,
                {"amount": format_naira(abs(result.residual))},
            )
        )

    if result.uncertainty and result.uncertainty > 0:
        lines.append("")
        lines.append(
            render_message(
                "query.insight.uncertain_value",
                language,
                {"amount": format_naira(result.uncertainty)},
            )
        )

    if result.coverage.value != "complete":
        lines.append("")
        coverage_key: MessageKey = (
            "query.insight.coverage_unavailable"
            if result.coverage.value == "unavailable"
            else "query.insight.coverage_partial"
        )
        lines.append(render_message(coverage_key, language))

    return "\n".join(lines)


def _selector_to_payload(selector: InsightEvidenceSelector) -> SelectionPayload:
    if selector.dimension == Dimension.COUNTERPARTY or selector.dimension == Dimension.ACCOUNT:
        pass

    return SelectionPayload(
        selection_kind="group_bucket",
        entity_type="variance_driver",
        entity_id=f"{selector.metric.value}:{selector.dimension.value}:{selector.bucket_key}",
        label=selector.bucket_key,
        insight_evidence=VarianceDriversEvidenceSelection(
            basis=selector.basis,
            measure=selector.measure,
            dimension=cast(Any, selector.dimension.value),
            bucket_key=selector.bucket_key,
            metric=selector.metric.value,
            current_start=selector.current_start.isoformat(),
            current_end=selector.current_end.isoformat(),
            baseline_start=selector.baseline_start.isoformat(),
            baseline_end=selector.baseline_end.isoformat(),
        ),
    )


def build_variance_surface_view(
    result: VarianceAnalysisResult,
    language: str = "en",
) -> SurfaceView:
    """Build a typed surface view for variance driver selection."""
    items: list[SurfaceItemView] = []
    for selector in result.evidence_selectors:
        driver = None
        for view in result.dimension_views:
            if view.metric != selector.metric:
                continue
            for d in view.drivers:
                if d.dimension == selector.dimension and d.key == selector.bucket_key:
                    driver = d
                    break
            if driver:
                break

        label = driver.label if driver else selector.bucket_key
        items.append(
            SurfaceItemView(
                id=f"{selector.metric.value}:{selector.dimension.value}:{selector.bucket_key}",
                label=str(label),
                amount=float(abs(driver.absolute_delta)) if driver else 0.0,
                payload=_selector_to_payload(selector),
                metadata={
                    "measure": selector.measure,
                    "dimension": selector.dimension.value,
                    "key": selector.bucket_key,
                    "basis": selector.basis,
                    "selector": selector.model_dump(),
                },
            )
        )

    return SurfaceView(
        mode=SurfaceViewMode.INSIGHT,
        items=items,
        lead_text=format_variance_result(result, language=language),
        context={"view": "variance_insight", "measure": result.measure, "basis": result.basis},
    )


def build_variance_query_items(result: VarianceAnalysisResult, language: str = "en") -> list[dict[str, Any]]:
    """Build QueryResultItem-compatible dicts for variance drivers."""
    items: list[dict[str, Any]] = []
    for view in result.dimension_views:
        for index, driver in enumerate(view.drivers, start=1):
            items.append(
                {
                    "id": f"{view.metric.value}-{view.dimension.value}-{driver.key}-{index}",
                    "description": _render_driver(driver, language),
                    "amount": float(abs(driver.absolute_delta)),
                    "date": result.current_period.end_date,
                    "metadata": {
                        "driver_kind": "dimension",
                        "metric": view.metric.value,
                        "dimension": view.dimension.value,
                        "key": driver.key,
                        "label": driver.label,
                        "current": float(driver.current_value),
                        "baseline": float(driver.baseline_value),
                        "change": float(driver.absolute_delta),
                        "relative_status": driver.relative_status,
                        "analysis_basis": result.basis,
                    },
                }
            )
    return items
