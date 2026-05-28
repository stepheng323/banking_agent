from __future__ import annotations

import json

import pytest

from scripts import readiness
from scripts.readiness_assertions import (
    assert_readiness_turn,
    task_types_from_response,
)
from scripts.readiness_models import (
    ReadinessExpectation,
    ReadinessInvocation,
    ReadinessScenario,
    ReadinessTurn,
)
from scripts.readiness_rendering import (
    duplicate_visible_blocks,
    render_orchestrator_result,
)
from scripts.readiness_report import write_json_report
from scripts.readiness_sequence import (
    run_readiness_sequence,
)


def test_render_orchestrator_result_handles_common_outbox_entries() -> None:
    rendered = render_orchestrator_result(
        {
            "outbox": [
                {"type": "say", "text": "Hello"},
                {"type": "request_confirmation", "header": "Confirm Transfer", "summary": "₦2,000 to Tolu"},
                {"type": "auth_request", "header": "Transfer Authorization", "summary": "Enter PIN"},
                {"type": "show_options", "title": "Pick one", "options": [{"label": "MTN 5GB"}, "MTN 3GB"]},
                {"type": "show_receipt", "caption": "Receipt ready", "receipt": {"id": "r1"}},
            ]
        }
    )

    assert "Hello" in rendered
    assert "Confirm Transfer\n₦2,000 to Tolu" in rendered
    assert "Transfer Authorization\nEnter PIN" in rendered
    assert "1. MTN 5GB" in rendered
    assert "2. MTN 3GB" in rendered
    assert "Receipt ready" in rendered


def test_assert_readiness_turn_checks_substrings_route_tasks_jobs_and_duplicates() -> None:
    turn = ReadinessTurn(
        "send 5k to Ada",
        ReadinessExpectation(
            expect_all=("Confirm", "Ada"),
            expect_none=("Xara",),
            expect_path_shape="deterministic_transfer_domain",
            expect_routing_owner="guardrail",
            expect_routing_decision="fresh_transfer_command",
            expect_task_types=("transfer",),
            expect_async_job_count_delta=0,
        ),
    )

    passed, errors = assert_readiness_turn(
        turn,
        "Confirm\nAda",
        route_metadata={
            "semantic_path_shape": "deterministic_transfer_domain",
            "routing_owner": "guardrail",
            "routing_decision": "fresh_transfer_command",
        },
        task_types=("transfer",),
        async_jobs=(),
    )

    assert passed
    assert errors == ()

    failed, duplicate_errors = assert_readiness_turn(
        turn,
        "Confirm\nAda\n\nConfirm\nAda",
        route_metadata={
            "semantic_path_shape": "deterministic_transfer_domain",
            "routing_owner": "guardrail",
            "routing_decision": "fresh_transfer_command",
        },
        task_types=("transfer",),
        async_jobs=(),
    )

    assert not failed
    assert any("duplicate visible response block" in error for error in duplicate_errors)


def test_duplicate_visible_blocks_returns_repeated_blocks() -> None:
    assert duplicate_visible_blocks("One\n\nTwo\n\nOne") == ("One",)
    assert duplicate_visible_blocks("One\n\nTwo") == ()


def test_task_types_from_response_falls_back_to_route_shape() -> None:
    assert task_types_from_response({"task_types": ["transfer"]}) == ("transfer",)
    assert task_types_from_response({"task_executors": ["data"]}) == ("data",)
    assert task_types_from_response({"routing_target_domain": "airtime"}) == ("airtime",)
    assert task_types_from_response({"semantic_path_shape": "deterministic_transfer_domain"}) == ("transfer",)
    assert task_types_from_response({"semantic_path_shape": "schedule_read_router_direct"}) == ("schedule",)
    assert task_types_from_response({"semantic_path_shape": "meta_direct"}) == ()


@pytest.mark.asyncio
async def test_run_readiness_sequence_stops_on_first_failure() -> None:
    scenario = ReadinessScenario(
        id="unit",
        turns=(
            ReadinessTurn("one", ReadinessExpectation(expect_all=("missing",))),
            ReadinessTurn("two", ReadinessExpectation(expect_all=("two",))),
        ),
    )
    calls: list[str] = []

    async def invoke_turn(
        scenario_arg: ReadinessScenario,
        turn: ReadinessTurn,
        index: int,
    ) -> ReadinessInvocation:
        del scenario_arg, index
        calls.append(turn.text)
        return ReadinessInvocation(response={"text": "not enough"})

    result = await run_readiness_sequence(
        mode="deterministic",
        scenarios=(scenario,),
        invoke_turn=invoke_turn,
        stop_on_fail=True,
    )

    assert calls == ["one"]
    assert not result.passed
    assert len(result.turns) == 1


@pytest.mark.asyncio
async def test_run_readiness_sequence_calls_before_each_scenario() -> None:
    scenarios = (
        ReadinessScenario(id="one", turns=(ReadinessTurn("hi"),)),
        ReadinessScenario(id="two", turns=(ReadinessTurn("hi"),)),
    )
    started: list[str] = []

    async def before_scenario(scenario: ReadinessScenario) -> None:
        started.append(scenario.id)

    async def invoke_turn(
        scenario_arg: ReadinessScenario,
        turn: ReadinessTurn,
        index: int,
    ) -> ReadinessInvocation:
        del scenario_arg, turn, index
        return ReadinessInvocation(response={"text": "Hi"})

    result = await run_readiness_sequence(
        mode="deterministic",
        scenarios=scenarios,
        invoke_turn=invoke_turn,
        before_scenario=before_scenario,
    )

    assert result.passed
    assert started == ["one", "two"]


@pytest.mark.asyncio
async def test_write_json_report_writes_serializable_result(tmp_path) -> None:
    scenario = ReadinessScenario(id="unit", turns=(ReadinessTurn("hi"),))

    async def invoke_turn(
        scenario_arg: ReadinessScenario,
        turn: ReadinessTurn,
        index: int,
    ) -> ReadinessInvocation:
        del scenario_arg, turn, index
        return ReadinessInvocation(response={"text": "Hi"})

    result = await run_readiness_sequence(
        mode="deterministic",
        scenarios=(scenario,),
        invoke_turn=invoke_turn,
    )
    report_path = tmp_path / "readiness" / "latest.json"

    write_json_report(result, report_path)

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["mode"] == "deterministic"
    assert payload["scenario_ids"] == ["unit"]
    assert payload["turn_count"] == 1


def test_readiness_cli_parses_expected_flags() -> None:
    args = readiness.parse_args(
        [
            "--mode",
            "dry-run",
            "--scenario",
            "data",
            "--phone",
            "2348162511023",
            "--channel",
            "whatsapp",
            "--seed",
            "--reset-session",
            "--json-output",
            ".readiness/latest.json",
        ]
    )

    assert args.mode == "dry-run"
    assert args.scenario == "data"
    assert args.phone == "2348162511023"
    assert args.channel == "whatsapp"
    assert args.seed is True
    assert args.reset_session is True
    assert args.json_output == ".readiness/latest.json"
