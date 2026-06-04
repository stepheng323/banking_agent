"""Pure eligibility helpers for gate handler traceability."""

from __future__ import annotations

from collections.abc import Iterable

from apps.chat.src.agent.orchestrator.workflows.gate.context import GateContext
from apps.chat.src.agent.orchestrator.workflows.gate.contracts import GateEligibility, GateEligibilityResult


def _result(eligible: bool, reason: str, details: dict[str, object] | None = None) -> GateEligibilityResult:
    return GateEligibilityResult(eligible=eligible, reason=reason, details=details or {})


def always(_: GateContext) -> GateEligibilityResult:
    return _result(True, "always")


def no_live_pending_interrupt(ctx: GateContext) -> GateEligibilityResult:
    return _result(
        not ctx.live_pending_interrupt,
        "no_live_pending_interrupt" if not ctx.live_pending_interrupt else "live_pending_interrupt",
        {"pending_interrupt_kind": ctx.state_view.pending_interrupt_kind},
    )


def no_gate_blocking_state(ctx: GateContext) -> GateEligibilityResult:
    return _result(
        not ctx.state_view.has_gate_blocking_state,
        "no_gate_blocking_state" if not ctx.state_view.has_gate_blocking_state else "gate_blocking_state",
        {
            "has_pending_interrupt": ctx.state_view.has_pending_interrupt,
            "has_quote": ctx.state_view.has_quote,
            "has_session_stack": ctx.state_view.has_session_stack,
            "has_waves": ctx.state_view.has_waves,
        },
    )


def no_quote(ctx: GateContext) -> GateEligibilityResult:
    return _result(
        not ctx.state_view.has_quote,
        "no_quote" if not ctx.state_view.has_quote else "has_quote",
    )


def task_planner_available(ctx: GateContext) -> GateEligibilityResult:
    return _result(
        ctx.task_planner is not None,
        "task_planner_available" if ctx.task_planner is not None else "task_planner_missing",
    )


def phrase_heavy_fastpath_allowed(ctx: GateContext) -> GateEligibilityResult:
    return _result(
        ctx.phrase_heavy_fastpath_allowed,
        "phrase_heavy_fastpath_allowed" if ctx.phrase_heavy_fastpath_allowed else "phrase_heavy_fastpath_blocked",
    )


def all_of(*checks: GateEligibility) -> GateEligibility:
    def _combined(ctx: GateContext) -> GateEligibilityResult:
        passed: list[str] = []
        for check in checks:
            result = check(ctx)
            if not result.eligible:
                return _result(
                    False,
                    result.reason,
                    {"failed": result.reason, "passed": passed, **result.details},
                )
            passed.append(result.reason)
        return _result(True, "all_of", {"passed": passed})

    return _combined


def any_of(*checks: GateEligibility) -> GateEligibility:
    def _combined(ctx: GateContext) -> GateEligibilityResult:
        failed: list[str] = []
        for check in checks:
            result = check(ctx)
            if result.eligible:
                return _result(True, "any_of", {"matched": result.reason, **result.details})
            failed.append(result.reason)
        return _result(False, "any_of_no_match", {"failed": failed})

    return _combined


def all_of_iterable(checks: Iterable[GateEligibility]) -> GateEligibility:
    return all_of(*tuple(checks))


__all__ = [
    "all_of",
    "all_of_iterable",
    "always",
    "any_of",
    "no_gate_blocking_state",
    "no_live_pending_interrupt",
    "no_quote",
    "phrase_heavy_fastpath_allowed",
    "task_planner_available",
]
