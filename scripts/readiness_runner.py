"""Hybrid readiness runner for scripted banking-agent transcripts."""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

# Allow direct execution via scripts that import this module.
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.graph.orchestrator import OrchestratorAgent
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.nodes.gate.runner import session_gate_direct_path
from apps.chat.src.runtime.chat_worker_dependencies import _build_orchestrator_runtime_bundle, _resolve_role_model
from scripts.seed_user_test_data import _resolve_target_user, _seed_for_user
from shared.assistant_profile.loader import get_cached_assistant_profile
from shared.cache.redis_client import RedisClient
from shared.config.settings import settings
from shared.guardrails.loader import get_cached_guardrails
from shared.i18n import validate_catalog_completeness
from shared.policy.loader import get_cached_policy
from shared.policy.validation import validate_policy_coverage
from shared.repositories.user_repository import UserRepository
from shared.services.task_planner import refresh_planner_system_prompt
from shared.services.unsupported_capabilities import UnsupportedBoundaryTurnOutput
from shared.types.planner import SemanticRouteDecision

ReadinessMode = Literal["deterministic", "dry-run"]
ReadinessScenarioName = Literal[
    "all",
    "core",
    "transfer",
    "data",
    "airtime",
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


class NoopPublisher:
    """Capture async jobs without publishing them to Redis/SQS."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        self.messages.append({"topic": topic, "message": message})


class DeterministicReadinessPlanner:
    """Bounded fake planner for CI-safe direct-path readiness scenarios."""

    def __init__(self) -> None:
        self.route_calls = 0
        self.boundary_calls = 0
        self.schedule_read_calls = 0

    async def route_semantic_turn(self, *args: object, **kwargs: object) -> SemanticRouteDecision:
        del args, kwargs
        self.route_calls += 1
        return SemanticRouteDecision(
            decision="direct_reply",
            confidence=0.2,
            response_key="conversational.clarify",
            response="Tell me what banking task you want to do next.",
            expected_transaction_executors=[],
            reason="deterministic readiness fallback",
        )

    async def classify_unsupported_boundary_turn(
        self,
        *args: object,
        **kwargs: object,
    ) -> UnsupportedBoundaryTurnOutput:
        del args, kwargs
        self.boundary_calls += 1
        return UnsupportedBoundaryTurnOutput(
            action="same_unsupported",
            capability_key="lending",
            confidence=0.91,
            reason="deterministic readiness unsupported continuation",
        )

    async def route_schedule_read_turn(self, *args: object, **kwargs: object) -> SemanticRouteDecision:
        del args, kwargs
        self.schedule_read_calls += 1
        return SemanticRouteDecision(
            decision="domain_schedule",
            target_intent="schedule",
            mode="new",
            confidence=0.91,
            schedule_response_mode="list",
            expected_transaction_executors=[],
            reason="deterministic readiness schedule read",
        )


def readiness_scenarios() -> dict[str, ReadinessScenario]:
    app_name_hint = settings.app_name_short.lower()
    return {
        "core": ReadinessScenario(
            id="core",
            description="Identity, brand, and conversational grounding checks.",
            turns=(
                ReadinessTurn("Hi", ReadinessExpectation(expect_any=("what would you like", "help", app_name_hint))),
                ReadinessTurn(
                    "Hi Xara",
                    ReadinessExpectation(
                        expect_all=("Not Xara", "I'm"),
                        expect_path_shape="meta_direct",
                        expect_routing_owner="guardrail",
                    ),
                ),
                ReadinessTurn(
                    "What is the meaning of Nenya?",
                    ReadinessExpectation(expect_any=("Ring of Water", "liquidity", "flow")),
                ),
                ReadinessTurn("Okay, that's mental", ReadinessExpectation(expect_none=("transfer to", "Amount:"))),
            ),
        ),
        "transfer": ReadinessScenario(
            id="transfer",
            description="Transfer start, interruption, and resume behavior.",
            turns=(
                ReadinessTurn(
                    "Send 2k to Tolu Adebayo",
                    ReadinessExpectation(
                        expect_path_shape="deterministic_transfer_domain",
                        expect_task_types=("transfer",),
                    ),
                ),
                ReadinessTurn(
                    "Wait, what's my Access balance?",
                    ReadinessExpectation(expect_any=("balance", "access")),
                    modes=("dry-run",),
                ),
                ReadinessTurn(
                    "Yes please",
                    ReadinessExpectation(expect_any=("transfer", "tolu", "continue", "review")),
                    modes=("dry-run",),
                ),
            ),
        ),
        "data": ReadinessScenario(
            id="data",
            description="Catalog-grounded data query, buy, and plan-edit checks.",
            turns=(
                ReadinessTurn(
                    "buy 1gb data for me",
                    ReadinessExpectation(
                        expect_path_shape="deterministic_data_domain",
                        expect_task_types=("data",),
                    ),
                    modes=("deterministic",),
                ),
                ReadinessTurn(
                    "How much is 5GB MTN?",
                    ReadinessExpectation(expect_any=("MTN", "5", "₦", "valid")),
                    modes=("dry-run",),
                ),
                ReadinessTurn(
                    "Buy it",
                    ReadinessExpectation(
                        expect_any=("Confirm Data", "data", "MTN", "5"),
                        expect_async_job_count_delta=0,
                    ),
                    modes=("dry-run",),
                ),
                ReadinessTurn(
                    "What other plan within that range?",
                    ReadinessExpectation(expect_any=("option", "plan", "reply")),
                    modes=("dry-run",),
                ),
                ReadinessTurn(
                    "option 2",
                    ReadinessExpectation(expect_any=("Confirm Data", "data", "₦")),
                    modes=("dry-run",),
                ),
            ),
        ),
        "airtime": ReadinessScenario(
            id="airtime",
            description="Airtime self and edit behavior.",
            turns=(
                ReadinessTurn(
                    "Buy me 1k airtime",
                    ReadinessExpectation(
                        expect_path_shape="deterministic_airtime_domain",
                        expect_task_types=("airtime",),
                    ),
                ),
                ReadinessTurn(
                    "make it 2k",
                    ReadinessExpectation(expect_any=("2,000", "airtime")),
                    modes=("dry-run",),
                ),
            ),
        ),
        "unsupported": ReadinessScenario(
            id="unsupported",
            description="Unsupported capability boundary and breakout checks.",
            turns=(
                ReadinessTurn(
                    "Can you borrow me money?",
                    ReadinessExpectation(expect_any=("can't help with loans", "can't help with lending")),
                ),
                ReadinessTurn(
                    "I will pay back",
                    ReadinessExpectation(
                        expect_any=("can't help with loans", "can't help with lending", "can’t help with loans"),
                        expect_none=("transfer to", "Amount:"),
                    ),
                ),
                ReadinessTurn(
                    "buy 1gb data for me",
                    ReadinessExpectation(expect_task_types=("data",), expect_path_shape="deterministic_data_domain"),
                ),
            ),
        ),
        "schedule": ReadinessScenario(
            id="schedule",
            description="Scheduled transaction read checks.",
            turns=(
                ReadinessTurn(
                    "Show my scheduled transactions",
                    ReadinessExpectation(expect_task_types=("schedule",)),
                ),
            ),
        ),
        "quick": ReadinessScenario(
            id="quick",
            description="Compatibility scenario from scripts.live_smoke.",
            turns=(
                ReadinessTurn("Hi", ReadinessExpectation(expect_any=("what would you like", "help", app_name_hint))),
                ReadinessTurn("Show my beneficiaries", ReadinessExpectation(expect_any=("beneficiar", "tolu"))),
                ReadinessTurn("Is that all?", ReadinessExpectation(expect_any=("3", "beneficiar", "saved"))),
                ReadinessTurn("Show my accounts", ReadinessExpectation(expect_any=("account", "bank"))),
                ReadinessTurn("Which one is GTBank?", ReadinessExpectation(expect_any=("gtbank", "0002", "account"))),
                ReadinessTurn(
                    "Why is Zenith pending?",
                    ReadinessExpectation(expect_any=("zenith", "pending", "authorization")),
                ),
                ReadinessTurn("Send 2k to tolu", ReadinessExpectation(expect_any=("tolu", "confirm", "which"))),
            ),
        ),
        "query": ReadinessScenario(
            id="query",
            description="Compatibility query scenario from scripts.live_smoke.",
            turns=(
                ReadinessTurn("Hi", ReadinessExpectation(expect_any=("what would you like", "help", app_name_hint))),
                ReadinessTurn(
                    "Show my recent transactions",
                    ReadinessExpectation(expect_any=("transaction", "showing", "sent", "received")),
                ),
                ReadinessTurn(
                    "Show the 25k one",
                    ReadinessExpectation(expect_any=("25,000", "transaction details", "adebayo", "bank")),
                ),
                ReadinessTurn(
                    "What bank was that?",
                    ReadinessExpectation(expect_any=("bank", "zenith", "first", "gtbank", "access")),
                ),
                ReadinessTurn(
                    "Now show the 3rd transaction",
                    ReadinessExpectation(expect_any=("transaction details", "amount", "bank")),
                ),
                ReadinessTurn("Back", ReadinessExpectation(expect_any=("transaction", "showing", "more", "page"))),
                ReadinessTurn("More", ReadinessExpectation(expect_any=("transaction", "showing", "more", "page"))),
            ),
        ),
    }


def resolve_scenarios(name: ReadinessScenarioName) -> tuple[ReadinessScenario, ...]:
    scenarios = readiness_scenarios()
    if name == "all":
        return tuple(scenarios[key] for key in ("core", "transfer", "data", "airtime", "unsupported", "schedule"))
    if name == "mvp":
        return tuple(scenarios[key] for key in ("quick", "transfer", "airtime"))
    if name == "query-deep":
        query = scenarios["query"]
        return (
            ReadinessScenario(
                id="query-deep",
                description="Compatibility deep query scenario from scripts.live_smoke.",
                turns=(
                    *query.turns,
                    ReadinessTurn(
                        "Is this all?",
                        ReadinessExpectation(expect_any=("coverage", "local", "synced", "confirm", "complete")),
                    ),
                    ReadinessTurn(
                        "Break down my spending by account this month",
                        ReadinessExpectation(expect_any=("breakdown", "account", "bank")),
                    ),
                    ReadinessTurn(
                        "Which account did I spend from most?",
                        ReadinessExpectation(expect_any=("account", "spent", "₦", "bank")),
                    ),
                    ReadinessTurn(
                        "How much did I spend on food this month?",
                        ReadinessExpectation(expect_any=("spent", "food", "₦", "transaction")),
                    ),
                    ReadinessTurn(
                        "Show the transactions behind that",
                        ReadinessExpectation(expect_any=("transaction", "showing", "sent", "received")),
                    ),
                    ReadinessTurn(
                        "Can I send 35k?",
                        ReadinessExpectation(expect_any=("cover", "₦35,000", "available", "shortfall", "breakdown")),
                    ),
                    ReadinessTurn(
                        "Why are Zenith transactions missing?",
                        ReadinessExpectation(expect_any=("zenith", "coverage", "authorization", "sync")),
                    ),
                ),
            ),
        )
    return (scenarios[name],)


def stringify_payload(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def intent_to_dict(intent: Any) -> dict[str, Any]:
    if hasattr(intent, "to_dict"):
        data = intent.to_dict()
        if isinstance(data, dict):
            return data
    if isinstance(intent, dict):
        return intent
    return {"type": type(intent).__name__, "value": str(intent)}


def render_orchestrator_result(result: dict[str, Any]) -> str:
    outbox = result.get("outbox") or []
    rendered: list[str] = []
    for entry in outbox:
        if not isinstance(entry, dict):
            continue
        entry_type = entry.get("type")
        if entry_type == "say":
            rendered.append(stringify_payload(entry.get("text")))
        elif entry_type == "request_confirmation":
            header = stringify_payload(entry.get("header"))
            summary = stringify_payload(entry.get("summary"))
            rendered.append("\n".join(part for part in (header, summary) if part))
        elif entry_type == "auth_request":
            header = stringify_payload(entry.get("header"))
            summary = stringify_payload(entry.get("summary"))
            rendered.append("\n".join(part for part in (header, summary) if part) or "PIN authorization requested.")
        elif entry_type == "show_options":
            title = stringify_payload(entry.get("title"))
            options = entry.get("options") or []
            option_lines = []
            for idx, option in enumerate(options, start=1):
                if isinstance(option, dict):
                    label = option.get("label") or option.get("title") or option.get("text") or option
                    option_lines.append(f"{idx}. {label}")
                else:
                    option_lines.append(f"{idx}. {option}")
            rendered.append("\n".join([title, *option_lines]).strip())
        elif entry_type == "show_receipt":
            receipt = entry.get("receipt") or {}
            caption = stringify_payload(entry.get("caption"))
            rendered.append("\n".join(part for part in (caption, str(receipt)) if part))
        else:
            rendered.append(str(entry))

    if rendered:
        return "\n\n".join(part for part in rendered if part).strip()

    intents = result.get("intents") or []
    for intent in intents:
        data = intent_to_dict(intent)
        rendered.append(
            "\n".join(
                part
                for part in (
                    stringify_payload(data.get("header")),
                    stringify_payload(data.get("summary")),
                    stringify_payload(data.get("text")),
                    stringify_payload(data.get("fallback_text")),
                )
                if part
            )
        )
    if rendered:
        return "\n\n".join(part for part in rendered if part).strip()

    return stringify_payload(result.get("text")).strip()


def duplicate_visible_blocks(response: str) -> tuple[str, ...]:
    blocks = tuple(part.strip() for part in response.split("\n\n") if part.strip())
    seen: set[str] = set()
    duplicates: list[str] = []
    for block in blocks:
        if block in seen and block not in duplicates:
            duplicates.append(block)
        seen.add(block)
    return tuple(duplicates)


def assert_readiness_turn(
    turn: ReadinessTurn,
    response: str,
    *,
    route_metadata: dict[str, Any] | None = None,
    task_types: tuple[str, ...] = (),
    async_jobs: tuple[dict[str, Any], ...] = (),
    enforce_route_expectations: bool = True,
) -> tuple[bool, tuple[str, ...]]:
    expectation = turn.expectation
    lowered = response.lower()
    errors: list[str] = []
    if expectation.expect_any and not any(expected.lower() in lowered for expected in expectation.expect_any):
        errors.append(f"expected any of: {', '.join(expectation.expect_any)}")
    for expected in expectation.expect_all:
        if expected.lower() not in lowered:
            errors.append(f"expected: {expected}")
    for forbidden in expectation.expect_none:
        if forbidden.lower() in lowered:
            errors.append(f"did not expect: {forbidden}")

    duplicates = duplicate_visible_blocks(response)
    if duplicates and not expectation.allow_duplicate_blocks:
        errors.append(f"duplicate visible response block: {duplicates[0]!r}")

    route_metadata = route_metadata or {}
    if enforce_route_expectations:
        route_expectations = {
            "semantic_path_shape": expectation.expect_path_shape,
            "routing_owner": expectation.expect_routing_owner,
            "routing_decision": expectation.expect_routing_decision,
        }
        for key, expected in route_expectations.items():
            if expected is not None and route_metadata.get(key) != expected:
                errors.append(f"expected {key}={expected!r}; got {route_metadata.get(key)!r}")

    if expectation.expect_task_types is not None and task_types != expectation.expect_task_types:
        errors.append(f"expected task types {expectation.expect_task_types}; got {task_types}")

    if (
        expectation.expect_async_job_count_delta is not None
        and len(async_jobs) != expectation.expect_async_job_count_delta
    ):
        errors.append(f"expected {expectation.expect_async_job_count_delta} async jobs; got {len(async_jobs)}")

    if expectation.expect_async_job_topics is not None:
        topics = tuple(str(job.get("topic") or "") for job in async_jobs)
        if topics != expectation.expect_async_job_topics:
            errors.append(f"expected async job topics {expectation.expect_async_job_topics}; got {topics}")

    return not errors, tuple(errors)


async def run_readiness_sequence(
    *,
    mode: ReadinessMode,
    scenarios: tuple[ReadinessScenario, ...],
    invoke_turn: Callable[[ReadinessScenario, ReadinessTurn, int], Awaitable[ReadinessInvocation]],
    before_scenario: Callable[[ReadinessScenario], Awaitable[None]] | None = None,
    stop_on_fail: bool = False,
    enforce_route_expectations: bool = True,
) -> ReadinessRunResult:
    results: list[ReadinessTurnResult] = []
    captured_async_jobs = 0
    for scenario in scenarios:
        if before_scenario is not None:
            await before_scenario(scenario)
        mode_turns = tuple(turn for turn in scenario.turns if mode in turn.modes)
        for index, turn in enumerate(mode_turns, start=1):
            started = time.perf_counter()
            invocation = await invoke_turn(scenario, turn, index)
            elapsed_ms = (time.perf_counter() - started) * 1000
            rendered = render_orchestrator_result(invocation.response)
            captured_async_jobs += len(invocation.async_jobs)
            passed, errors = assert_readiness_turn(
                turn,
                rendered,
                route_metadata=invocation.route_metadata,
                task_types=invocation.task_types,
                async_jobs=invocation.async_jobs,
                enforce_route_expectations=enforce_route_expectations,
            )
            result = ReadinessTurnResult(
                scenario_id=scenario.id,
                turn_index=index,
                user_text=turn.text,
                response_text=rendered,
                latency_ms=elapsed_ms,
                passed=passed,
                errors=errors,
                route_metadata=invocation.route_metadata,
                task_types=invocation.task_types,
                async_jobs=invocation.async_jobs,
            )
            results.append(result)
            if stop_on_fail and not passed:
                return ReadinessRunResult(
                    mode=mode,
                    scenario_ids=tuple(scenario.id for scenario in scenarios),
                    turns=tuple(results),
                    captured_async_jobs=captured_async_jobs,
                )
    return ReadinessRunResult(
        mode=mode,
        scenario_ids=tuple(scenario.id for scenario in scenarios),
        turns=tuple(results),
        captured_async_jobs=captured_async_jobs,
    )


def _route_metadata_from_state(state: OrchestratorState) -> dict[str, Any]:
    return {
        "semantic_path_shape": state.semantic_path_shape,
        "routing_owner": state.routing_owner,
        "routing_decision": state.routing_decision,
        "routing_target_domain": state.routing_target_domain,
        "routing_mode": state.routing_mode,
        "route_source": state.route_source,
    }


def task_types_from_response(response: dict[str, Any]) -> tuple[str, ...]:
    """Infer readiness task labels from public orchestrator response metadata."""

    task_types_raw = response.get("task_types")
    if isinstance(task_types_raw, list):
        return tuple(str(item) for item in task_types_raw)

    task_executors_raw = response.get("task_executors")
    if isinstance(task_executors_raw, list):
        return tuple(str(item) for item in task_executors_raw)

    expected_raw = response.get("expected_transaction_executors")
    if isinstance(expected_raw, list):
        return tuple(str(item) for item in expected_raw)

    route_domain = response.get("routing_target_domain")
    if isinstance(route_domain, str) and route_domain:
        return (route_domain,)

    path_shape = response.get("semantic_path_shape")
    if not isinstance(path_shape, str):
        return ()
    if "transfer" in path_shape:
        return ("transfer",)
    if "airtime" in path_shape:
        return ("airtime",)
    if "data" in path_shape:
        return ("data",)
    if "schedule" in path_shape:
        return ("schedule",)
    if "beneficiary" in path_shape:
        return ("beneficiary",)
    if "account" in path_shape or "balance" in path_shape:
        return ("account",)
    if "query" in path_shape:
        return ("query",)
    if "support" in path_shape:
        return ("support",)
    return ()


def _base_deterministic_state(*, scenario_id: str) -> OrchestratorState:
    state = OrchestratorState(
        user_id=f"readiness_{scenario_id}",
        phone_number="2348162511023",
        channel="whatsapp",
        loaded_context={"language": "en"},
    )
    if scenario_id == "unsupported":
        state = state.model_copy(
            update={
                "capability_boundary": None,
                "context_frames": [
                    ContextFrame(
                        frame_id="readiness-stale-transfer",
                        frame_type=ContextFrameType.GENERIC,
                        items=[
                            ContextEntity(
                                entity_id="stale-transfer",
                                entity_type=EntityType.GENERIC,
                                label="Stale transfer",
                                data={"summary": "₦2,000 transfer to Tolu Adebayo"},
                            )
                        ],
                        created_at_ts=int(time.time()),
                    )
                ],
            }
        )
    return state


async def run_deterministic_readiness(
    *,
    scenario_name: ReadinessScenarioName,
    stop_on_fail: bool = False,
) -> ReadinessRunResult:
    scenarios = resolve_scenarios(scenario_name)
    planner = DeterministicReadinessPlanner()
    states = {scenario.id: _base_deterministic_state(scenario_id=scenario.id) for scenario in scenarios}

    async def invoke_turn(scenario: ReadinessScenario, turn: ReadinessTurn, index: int) -> ReadinessInvocation:
        del index
        state = states[scenario.id]
        per_turn_reset = {
            "final_response": None,
            "policy_notice": None,
            "direct_path_triggered": False,
            "semantic_path_shape": None,
            "routing_owner": None,
            "routing_decision": None,
            "routing_target_domain": None,
            "routing_mode": None,
            "route_source": None,
            "routing_heuristic_type": None,
            "routing_heuristic_name": None,
            "planner_used": False,
            "suppress_empty_fallback": False,
        }
        state = state.model_copy(update={**per_turn_reset, "last_message_text": turn.text})
        updates = await session_gate_direct_path(
            state,
            {
                "configurable": {
                    "task_planner": planner,
                },
                "recursion_limit": 50,
            },
        )
        state = state.model_copy(update=updates)
        states[scenario.id] = state
        return ReadinessInvocation(
            response={"outbox": state.outbox, "text": state.final_response},
            route_metadata=_route_metadata_from_state(state),
            task_types=tuple(task.type for task in state.tasks.values()),
        )

    return await run_readiness_sequence(
        mode="deterministic",
        scenarios=scenarios,
        invoke_turn=invoke_turn,
        stop_on_fail=stop_on_fail,
        enforce_route_expectations=True,
    )


async def reset_redis_session(*, redis_client: Any, phone: str, channel: str) -> int:
    patterns = [
        f"checkpoint:{channel}:{phone}:*",
        f"checkpoint_write:{channel}:{phone}:*",
        f"write_keys_zset:{channel}:{phone}:*",
        f"user:{phone}:chat_history",
        f"query:session:{phone}",
        f"context_frames:{phone}",
    ]
    deleted = 0
    for pattern in patterns:
        keys = [key async for key in redis_client.scan_iter(match=pattern)]
        if keys:
            deleted += int(await redis_client.delete(*keys))
    return deleted


def _build_chat_model(*, role: str, model: str, timeout: float) -> Any:
    from langchain_openai import ChatOpenAI

    print(f"[setup] {role} model: {model}")
    return ChatOpenAI(model=model, temperature=0, timeout=timeout, max_retries=1)


async def build_dry_run_agent() -> tuple[UserRepository, OrchestratorAgent, NoopPublisher, Any]:
    validate_catalog_completeness()
    capability_policy = get_cached_policy(force_reload=True)
    validate_policy_coverage(capability_policy)
    get_cached_assistant_profile(force_reload=True)
    get_cached_guardrails(force_reload=True)
    refresh_planner_system_prompt()

    shared_redis = RedisClient.get_client()
    publisher = NoopPublisher()
    planner_llm = _build_chat_model(role="planner", model=settings.planner_model, timeout=30.0)
    app_env = settings.runtime.app_env
    query_model = _resolve_role_model(
        role="query",
        configured_model=settings.query_model,
        planner_model=settings.planner_model,
        app_env=app_env,
    )
    semantic_router_model = _resolve_role_model(
        role="semantic_router",
        configured_model=settings.semantic_router_model,
        planner_model=settings.planner_model,
        app_env=app_env,
    )
    interrupt_model = _resolve_role_model(
        role="interrupt_router",
        configured_model=settings.interrupt_router_model,
        planner_model=settings.planner_model,
        app_env=app_env,
    )
    extractor_model = _resolve_role_model(
        role="extractor",
        configured_model=settings.extractor_model,
        planner_model=settings.planner_model,
        app_env=app_env,
    )

    user_repo, _onboarding_executor, agent = _build_orchestrator_runtime_bundle(
        queue_publisher=publisher,
        messaging_clients={},
        shared_redis=shared_redis,
        llm=planner_llm,
        query_llm=_build_chat_model(role="query", model=query_model, timeout=30.0),
        semantic_router_llm=_build_chat_model(role="semantic_router", model=semantic_router_model, timeout=15.0),
        interrupt_llm=_build_chat_model(role="interrupt_router", model=interrupt_model, timeout=15.0),
        extractor_llm=_build_chat_model(role="extractor", model=extractor_model, timeout=20.0),
    )

    async def _noop_progress_updates(**_: Any) -> None:
        return None

    agent.orchestrator_handler._run_progress_updates = _noop_progress_updates
    return user_repo, agent, publisher, shared_redis


async def run_dry_run_readiness(
    *,
    scenario_name: ReadinessScenarioName,
    phone: str,
    channel: str = "telegram",
    channel_user_id: str | None = None,
    seed: bool = False,
    reset_session: bool = False,
    stop_on_fail: bool = False,
) -> ReadinessRunResult:
    target_user = await _resolve_target_user(phone)
    if seed:
        await _seed_for_user(target_user)

    user_repo, agent, publisher, redis_client = await build_dry_run_agent()
    user = await user_repo.get_by_phone(target_user.phone_number)
    if user is None:
        raise RuntimeError(f"Could not load user {target_user.phone_number}")

    scenarios = resolve_scenarios(scenario_name)
    run_id = uuid.uuid4().hex[:8]
    reset_scenarios: set[str] = set()

    async def before_scenario(scenario: ReadinessScenario) -> None:
        if not reset_session or scenario.id in reset_scenarios:
            return
        deleted = await reset_redis_session(redis_client=redis_client, phone=target_user.phone_number, channel=channel)
        print(f"[setup] reset Redis session keys before {scenario.id}: {deleted}")
        reset_scenarios.add(scenario.id)

    async def invoke_turn(scenario: ReadinessScenario, turn: ReadinessTurn, index: int) -> ReadinessInvocation:
        before_jobs = len(publisher.messages)
        message_id = f"readiness-{run_id}-{scenario.id}-{index}-{int(time.time() * 1000)}"
        response = await agent.invoke(
            phone_number=target_user.phone_number,
            text=turn.text,
            message_id=message_id,
            channel=channel,
            channel_identity=channel_user_id,
            user=user,
        )
        if turn.pin_after:
            pin_response = await agent.resume_transaction(
                phone_number=target_user.phone_number,
                flow_type=turn.pin_flow_type,
                pin_verified=True,
                channel=channel,
            )
            response = {
                **response,
                "outbox": [*(response.get("outbox") or []), *(pin_response.get("outbox") or [])],
                "intents": [*(response.get("intents") or []), *(pin_response.get("intents") or [])],
                "text": "\n\n".join(
                    part
                    for part in (
                        stringify_payload(response.get("text")),
                        stringify_payload(pin_response.get("text")),
                    )
                    if part
                ),
            }
        async_jobs = tuple(publisher.messages[before_jobs:])
        route_metadata = {
            key: response.get(key)
            for key in (
                "semantic_path_shape",
                "routing_owner",
                "routing_decision",
                "routing_target_domain",
                "routing_mode",
                "route_source",
            )
            if key in response
        }
        return ReadinessInvocation(
            response=response,
            route_metadata=route_metadata,
            task_types=task_types_from_response(response),
            async_jobs=async_jobs,
        )

    return await run_readiness_sequence(
        mode="dry-run",
        scenarios=scenarios,
        invoke_turn=invoke_turn,
        before_scenario=before_scenario,
        stop_on_fail=stop_on_fail,
        enforce_route_expectations=False,
    )


def write_json_report(result: ReadinessRunResult, path: str | Path) -> None:
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True), encoding="utf-8")


def print_readiness_report(result: ReadinessRunResult) -> None:
    print("\n=== Readiness Transcript ===")
    for turn in result.turns:
        status = "PASS" if turn.passed else "FAIL"
        print(f"[{turn.scenario_id} #{turn.turn_index}] USER: {turn.user_text}")
        print(f"{settings.app_name_short.upper()} ({turn.latency_ms:.0f}ms):")
        print(turn.response_text or "[no visible response]")
        if turn.errors:
            print("Errors:")
            for error in turn.errors:
                print(f"- {error}")
        print(f"[{status}]\n")

    print("=== Readiness Summary ===")
    print(
        " ".join(
            (
                f"mode={result.mode}",
                f"scenarios={','.join(result.scenario_ids)}",
                f"turns={len(result.turns)}",
                f"passed={sum(1 for turn in result.turns if turn.passed)}",
                f"failed={len(result.failed_turns)}",
                f"captured_async_jobs={result.captured_async_jobs}",
            )
        )
    )
    if result.failed_turns:
        for turn in result.failed_turns:
            print(f"- {turn.scenario_id} #{turn.turn_index} {turn.user_text!r}: {'; '.join(turn.errors)}")


async def run_readiness(
    *,
    mode: ReadinessMode,
    scenario: ReadinessScenarioName,
    phone: str | None = None,
    channel: str = "telegram",
    channel_user_id: str | None = None,
    seed: bool = False,
    reset_session: bool = False,
    stop_on_fail: bool = False,
) -> ReadinessRunResult:
    if mode == "deterministic":
        return await run_deterministic_readiness(scenario_name=scenario, stop_on_fail=stop_on_fail)
    if not phone:
        raise ValueError("--phone is required in dry-run mode")
    return await run_dry_run_readiness(
        scenario_name=scenario,
        phone=phone,
        channel=channel,
        channel_user_id=channel_user_id,
        seed=seed,
        reset_session=reset_session,
        stop_on_fail=stop_on_fail,
    )


def run_readiness_sync(
    *,
    mode: ReadinessMode,
    scenario: ReadinessScenarioName,
    phone: str | None = None,
    channel: str = "telegram",
    channel_user_id: str | None = None,
    seed: bool = False,
    reset_session: bool = False,
    stop_on_fail: bool = False,
    json_output: str | None = None,
) -> int:
    result = asyncio.run(
        run_readiness(
            mode=mode,
            scenario=scenario,
            phone=phone,
            channel=channel,
            channel_user_id=channel_user_id,
            seed=seed,
            reset_session=reset_session,
            stop_on_fail=stop_on_fail,
        )
    )
    print_readiness_report(result)
    if json_output:
        write_json_report(result, json_output)
        print(f"[report] wrote {json_output}")
    return 0 if result.passed else 1
