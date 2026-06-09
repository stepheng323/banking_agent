"""Internal planner quality diagnostics.

These models are intentionally not part of the LLM-facing PlannerOutput schema.
"""

from __future__ import annotations

from dataclasses import dataclass

from shared.types.planner import PlannerOutput


@dataclass(frozen=True)
class PlannerQualityReport:
    """Per-turn planner cleanliness result."""

    dirty_reasons: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        return not self.dirty_reasons

    def with_reason(self, reason: str | None) -> PlannerQualityReport:
        normalized = str(reason or "").strip()
        if not normalized or normalized in self.dirty_reasons:
            return self
        return PlannerQualityReport(dirty_reasons=(*self.dirty_reasons, normalized))

    def with_reasons(self, reasons: list[str] | tuple[str, ...]) -> PlannerQualityReport:
        report = self
        for reason in reasons:
            report = report.with_reason(reason)
        return report

    def to_state_updates(self) -> dict[str, object]:
        return {
            "planner_clean": self.clean,
            "planner_dirty_reasons": list(self.dirty_reasons),
        }


@dataclass(frozen=True)
class PlannerPlanResult:
    """Raw and normalized planner output plus quality diagnostics."""

    raw_output: PlannerOutput
    planner_output: PlannerOutput
    quality_report: PlannerQualityReport


@dataclass(frozen=True)
class PlannerPostprocessResult:
    """Postprocessed planner output plus updated quality diagnostics."""

    planner_output: PlannerOutput
    quality_report: PlannerQualityReport


__all__ = [
    "PlannerPlanResult",
    "PlannerPostprocessResult",
    "PlannerQualityReport",
]
