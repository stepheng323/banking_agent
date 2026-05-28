"""Data models for readiness transcript runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ReadinessMode = Literal["deterministic", "dry-run"]
ReadinessScenarioName = Literal[
    "all",
    "core",
    "transfer",
    "data",
    "airtime",
    "faq",
    "unsupported",
    "schedule",
    "quick",
    "mvp",
    "query",
    "query-deep",
]


@dataclass(frozen=True)
class ReadinessExpectation:
    expect_any: tuple[str, ...] = ()
    expect_all: tuple[str, ...] = ()
    expect_none: tuple[str, ...] = ()
    expect_path_shape: str | None = None
    expect_routing_owner: str | None = None
    expect_routing_decision: str | None = None
    expect_task_types: tuple[str, ...] | None = None
    expect_async_job_count_delta: int | None = None
    expect_async_job_topics: tuple[str, ...] | None = None
    allow_duplicate_blocks: bool = False


@dataclass(frozen=True)
class ReadinessTurn:
    text: str
    expectation: ReadinessExpectation = field(default_factory=ReadinessExpectation)
    pin_after: bool = False
    pin_flow_type: str = "transaction"
    modes: tuple[ReadinessMode, ...] = ("deterministic", "dry-run")


@dataclass(frozen=True)
class ReadinessScenario:
    id: str
    turns: tuple[ReadinessTurn, ...]
    description: str = ""


@dataclass(frozen=True)
class ReadinessInvocation:
    response: dict[str, Any]
    route_metadata: dict[str, Any] = field(default_factory=dict)
    task_types: tuple[str, ...] = ()
    async_jobs: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ReadinessTurnResult:
    scenario_id: str
    turn_index: int
    user_text: str
    response_text: str
    latency_ms: float
    passed: bool
    errors: tuple[str, ...] = ()
    route_metadata: dict[str, Any] = field(default_factory=dict)
    task_types: tuple[str, ...] = ()
    async_jobs: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "turn_index": self.turn_index,
            "user_text": self.user_text,
            "response_text": self.response_text,
            "latency_ms": round(self.latency_ms, 2),
            "passed": self.passed,
            "errors": list(self.errors),
            "route_metadata": self.route_metadata,
            "task_types": list(self.task_types),
            "async_jobs": list(self.async_jobs),
        }


@dataclass(frozen=True)
class ReadinessRunResult:
    mode: ReadinessMode
    scenario_ids: tuple[str, ...]
    turns: tuple[ReadinessTurnResult, ...]
    captured_async_jobs: int = 0

    @property
    def passed(self) -> bool:
        return all(turn.passed for turn in self.turns)

    @property
    def failed_turns(self) -> tuple[ReadinessTurnResult, ...]:
        return tuple(turn for turn in self.turns if not turn.passed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "scenario_ids": list(self.scenario_ids),
            "passed": self.passed,
            "turn_count": len(self.turns),
            "passed_count": sum(1 for turn in self.turns if turn.passed),
            "failed_count": len(self.failed_turns),
            "captured_async_jobs": self.captured_async_jobs,
            "turns": [turn.to_dict() for turn in self.turns],
        }
