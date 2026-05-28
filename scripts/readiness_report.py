"""Readiness report output helpers."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.readiness_models import ReadinessRunResult
from shared.config.settings import settings


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
