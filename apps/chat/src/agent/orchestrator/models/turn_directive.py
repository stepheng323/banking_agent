"""Canonical routing contract for one orchestrator turn."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, field_validator, model_validator

from shared.types.planner import RouterDomainIntent, SemanticRoutingMode


class RoutingContractError(RuntimeError):
    """Raised when a routing producer returns an invalid committed outcome."""


class TurnOutcomeKind(str, Enum):
    """Externally meaningful shape of the selected routing outcome."""

    DIRECT_RESPONSE = "direct_response"
    TASK_DISPATCH = "task_dispatch"
    PLANNER_HANDOFF = "planner_handoff"
    INTERRUPT_HANDOFF = "interrupt_handoff"
    POLICY_BLOCK = "policy_block"


class TurnNextStep(str, Enum):
    """The sole graph-control instruction emitted by routing nodes."""

    PLAN = "plan"
    HANDLE_INTERRUPT = "handle_interrupt"
    ADVANCE = "advance"
    FINALIZE = "finalize"
    END = "end"


TurnOwner = Literal[
    "guardrail",
    "semantic_router",
    "planner",
    "query_session",
    "interrupt",
]


class TurnDirective(BaseModel):
    """Authoritative semantic identity and graph disposition for a turn.

    ``owner`` is the component that made the current decision. It is never the
    component named by ``next_step``. Planner handoffs therefore retain their
    producing owner until the planner commits a replacement directive.
    """

    owner: TurnOwner
    decision: str
    outcome_kind: TurnOutcomeKind
    next_step: TurnNextStep
    target_domain: RouterDomainIntent | Literal["orchestrator", "transaction"] | None = None
    mode: SemanticRoutingMode | Literal["expired"] | None = None
    source: str
    path_shape: str
    heuristic_type: str | None = None
    heuristic_name: str | None = None

    @field_validator("decision", "source", "path_shape")
    @classmethod
    def require_nonempty_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("routing identity fields must be non-empty")
        return normalized

    @model_validator(mode="after")
    def validate_disposition(self) -> TurnDirective:
        allowed: dict[TurnOutcomeKind, set[TurnNextStep]] = {
            TurnOutcomeKind.DIRECT_RESPONSE: {TurnNextStep.FINALIZE, TurnNextStep.END},
            TurnOutcomeKind.POLICY_BLOCK: {TurnNextStep.FINALIZE, TurnNextStep.END},
            TurnOutcomeKind.TASK_DISPATCH: {TurnNextStep.ADVANCE, TurnNextStep.FINALIZE, TurnNextStep.END},
            TurnOutcomeKind.PLANNER_HANDOFF: {TurnNextStep.PLAN},
            TurnOutcomeKind.INTERRUPT_HANDOFF: {TurnNextStep.HANDLE_INTERRUPT},
        }
        if self.next_step not in allowed[self.outcome_kind]:
            raise ValueError(
                f"{self.outcome_kind.value} cannot transition to {self.next_step.value}"
            )
        return self


_CONTROLLED_UPDATE_KEYS = {
    "turn_directive",
    "direct_path_triggered",
    "semantic_path_shape",
    "path_shape",
    "routing_owner",
    "routing_decision",
    "routing_target_domain",
    "routing_mode",
    "route_source",
    "routing_heuristic_type",
    "routing_heuristic_name",
}


def _default_next_step(outcome_kind: TurnOutcomeKind) -> TurnNextStep:
    if outcome_kind in {TurnOutcomeKind.DIRECT_RESPONSE, TurnOutcomeKind.POLICY_BLOCK}:
        return TurnNextStep.FINALIZE
    if outcome_kind == TurnOutcomeKind.TASK_DISPATCH:
        return TurnNextStep.ADVANCE
    if outcome_kind == TurnOutcomeKind.PLANNER_HANDOFF:
        return TurnNextStep.PLAN
    return TurnNextStep.HANDLE_INTERRUPT


def build_turn_directive(
    *,
    owner: TurnOwner,
    decision: str,
    outcome_kind: TurnOutcomeKind,
    next_step: TurnNextStep | None = None,
    target_domain: RouterDomainIntent | Literal["orchestrator", "transaction"] | None = None,
    mode: SemanticRoutingMode | Literal["expired"] | None = None,
    source: str | None = None,
    path_shape: str | None = None,
    heuristic_type: str | None = None,
    heuristic_name: str | None = None,
) -> TurnDirective:
    """Construct a validated directive through the canonical factory."""

    resolved_source = source or owner
    return TurnDirective(
        owner=owner,
        decision=decision,
        outcome_kind=outcome_kind,
        next_step=next_step or _default_next_step(outcome_kind),
        target_domain=target_domain,
        mode=mode,
        source=resolved_source,
        path_shape=path_shape or resolved_source,
        heuristic_type=heuristic_type,
        heuristic_name=heuristic_name,
    )


@dataclass(frozen=True, slots=True)
class RouteResolution(Mapping[str, Any]):
    """A committed directive plus its atomic LangGraph state patch."""

    directive: TurnDirective
    updates: Mapping[str, Any]

    def __getitem__(self, key: str) -> Any:
        if key == "turn_directive":
            return self.directive
        return self.updates[key]

    def __iter__(self):  # type: ignore[no-untyped-def]
        yield from self.updates
        if "turn_directive" not in self.updates:
            yield "turn_directive"

    def __len__(self) -> int:
        return len(self.updates) + ("turn_directive" not in self.updates)

    def with_updates(self, updates: Mapping[str, Any]) -> RouteResolution:
        """Return this semantic decision with additional non-routing state."""
        return RouteResolution(
            directive=self.directive,
            updates={**self.updates, **updates},
        )

    def materialize(self, *, base_state: object | None = None) -> dict[str, Any]:
        """Validate and atomically commit this resolution.

        ``base_state`` is accepted only for transition nodes that preserve an
        already-visible response, live interrupt, or current execution wave.
        Initial route producers must provide those values in ``updates``.
        """
        conflicting = _CONTROLLED_UPDATE_KEYS.intersection(self.updates)
        if conflicting:
            names = ", ".join(sorted(conflicting))
            raise RoutingContractError(f"route payload overrides controlled fields: {names}")

        updates = dict(self.updates)
        visible_response = _has_visible_output(updates)
        if not visible_response and base_state is not None:
            visible_response = _has_visible_output(
                {
                    "final_response": getattr(base_state, "final_response", None),
                    "outbox": getattr(base_state, "outbox", None),
                }
            )
        if self.directive.outcome_kind in {
            TurnOutcomeKind.DIRECT_RESPONSE,
            TurnOutcomeKind.POLICY_BLOCK,
        } and not visible_response:
            raise RoutingContractError("response outcomes require final_response or outbox")

        if (
            self.directive.outcome_kind == TurnOutcomeKind.TASK_DISPATCH
            and self.directive.next_step == TurnNextStep.ADVANCE
        ):
            waves = updates.get("waves", getattr(base_state, "waves", None))
            index = updates.get(
                "current_wave_index",
                getattr(base_state, "current_wave_index", 0),
            )
            if not _has_current_wave(waves, index):
                raise RoutingContractError("task dispatch requires a valid current wave")

        if self.directive.outcome_kind == TurnOutcomeKind.PLANNER_HANDOFF:
            forbidden = {
                key
                for key in ("final_response", "tasks", "waves")
                if updates.get(key)
            }
            if forbidden:
                names = ", ".join(sorted(forbidden))
                raise RoutingContractError(f"planner handoff contains terminal payload: {names}")

        if self.directive.outcome_kind == TurnOutcomeKind.INTERRUPT_HANDOFF:
            interrupt = updates.get(
                "pending_interrupt",
                getattr(base_state, "pending_interrupt", None),
            )
            if interrupt is None:
                raise RoutingContractError("interrupt handoff requires pending_interrupt")

        updates["turn_directive"] = self.directive
        return updates


def route_resolution(
    *,
    updates: Mapping[str, Any],
    owner: TurnOwner,
    decision: str,
    outcome_kind: TurnOutcomeKind,
    next_step: TurnNextStep | None = None,
    target_domain: RouterDomainIntent | Literal["orchestrator", "transaction"] | None = None,
    mode: SemanticRoutingMode | Literal["expired"] | None = None,
    source: str | None = None,
    path_shape: str | None = None,
    heuristic_type: str | None = None,
    heuristic_name: str | None = None,
) -> RouteResolution:
    """Build the only supported route-resolution envelope."""

    return RouteResolution(
        directive=build_turn_directive(
            owner=owner,
            decision=decision,
            outcome_kind=outcome_kind,
            next_step=next_step,
            target_domain=target_domain,
            mode=mode,
            source=source,
            path_shape=path_shape,
            heuristic_type=heuristic_type,
            heuristic_name=heuristic_name,
        ),
        updates=updates,
    )


def advance_directive(directive: TurnDirective, next_step: TurnNextStep) -> TurnDirective:
    """Advance graph disposition without changing semantic route identity."""

    return build_turn_directive(
        owner=directive.owner,
        decision=directive.decision,
        outcome_kind=directive.outcome_kind,
        next_step=next_step,
        target_domain=directive.target_domain,
        mode=directive.mode,
        source=directive.source,
        path_shape=directive.path_shape,
        heuristic_type=directive.heuristic_type,
        heuristic_name=directive.heuristic_name,
    )


def _has_current_wave(waves: object, index: object) -> bool:
    if not isinstance(waves, Sequence) or isinstance(waves, (str, bytes)):
        return False
    if not isinstance(index, int) or index < 0 or index >= len(waves):
        return False
    wave = waves[index]
    return isinstance(wave, Sequence) and not isinstance(wave, (str, bytes)) and bool(wave)


def _has_visible_output(values: Mapping[str, Any]) -> bool:
    response = values.get("final_response")
    if isinstance(response, str) and response.strip():
        return True
    outbox = values.get("outbox")
    if not isinstance(outbox, Sequence) or isinstance(outbox, (str, bytes)):
        return False
    return any(
        isinstance(item, Mapping)
        and any(isinstance(value, str) and value.strip() for value in item.values())
        for item in outbox
    )


__all__ = [
    "RouteResolution",
    "RoutingContractError",
    "TurnDirective",
    "TurnNextStep",
    "TurnOutcomeKind",
    "advance_directive",
    "build_turn_directive",
    "route_resolution",
]
