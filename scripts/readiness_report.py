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


def write_text_report(result: ReadinessRunResult, path: str | Path) -> None:
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(format_readiness_report(result), encoding="utf-8")


def format_readiness_report(result: ReadinessRunResult) -> str:
    lines: list[str] = ["", "=== Readiness Transcript ==="]
    for turn in result.turns:
        status = "PASS" if turn.passed else "FAIL"
        lines.append(f"[{turn.scenario_id} #{turn.turn_index}] USER: {turn.user_text}")
        lines.append(f"{settings.app_name_short.upper()} ({turn.latency_ms:.0f}ms):")
        lines.append(turn.response_text or "[no visible response]")
        if turn.planner_clean is not None:
            clean_label = "yes" if turn.planner_clean else "no"
            if turn.planner_dirty_reasons:
                lines.append(f"Planner clean: {clean_label} ({', '.join(turn.planner_dirty_reasons)})")
            else:
                lines.append(f"Planner clean: {clean_label}")
        if turn.llm_calls:
            llm_total = sum(float(call.get("duration_ms") or 0.0) for call in turn.llm_calls)
            slowest = max(turn.llm_calls, key=lambda call: float(call.get("duration_ms") or 0.0))
            lines.append(
                " ".join(
                    (
                        f"LLM calls: {len(turn.llm_calls)}",
                        f"total_ms={llm_total:.0f}",
                        f"slowest={slowest.get('event_name', 'unknown')}",
                        f"slowest_ms={float(slowest.get('duration_ms') or 0.0):.0f}",
                    )
                )
            )
        if turn.llm_budget is not None:
            lines.append(
                " ".join(
                    (
                        f"LLM budget: {turn.llm_budget_status}",
                        f"route={turn.to_dict()['route_signature']}",
                        f"chain={','.join(turn.to_dict()['llm_event_chain']) or 'none'}",
                    )
                )
            )
            for violation in turn.llm_budget_violations:
                lines.append(f"- LLM budget detail: {violation}")
        if turn.errors:
            lines.append("Errors:")
            for error in turn.errors:
                lines.append(f"- {error}")
        lines.append(f"[{status}]")
        lines.append("")

    lines.append("=== Readiness Summary ===")
    lines.append(
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
    latency = result.latency_summary
    planner_quality = result.planner_quality_summary
    llm_summary = result.llm_call_summary
    llm_health = result.llm_health_summary
    robustness = result.robustness_summary
    lines.append(
        " ".join(
            (
                f"robustness_pass_rate={robustness['pass_rate']}",
                f"unsafe_execution_count={robustness['unsafe_execution_count']}",
                f"outcomes={robustness['by_outcome']}",
            )
        )
    )
    lines.append(
        " ".join(
            (
                f"latency_min_ms={latency['min_ms']}",
                f"latency_avg_ms={latency['avg_ms']}",
                f"latency_p50_ms={latency['p50_ms']}",
                f"latency_p95_ms={latency['p95_ms']}",
                f"latency_p99_ms={latency['p99_ms']}",
                f"latency_max_ms={latency['max_ms']}",
            )
        )
    )
    lines.append(
        " ".join(
            (
                f"planner_quality_turns={planner_quality['turn_count']}",
                f"planner_clean={planner_quality['clean_count']}",
                f"planner_dirty={planner_quality['dirty_count']}",
                f"planner_clean_rate={planner_quality['clean_rate']}",
            )
        )
    )
    if llm_summary["call_count"]:
        lines.append(
            " ".join(
                (
                    f"llm_calls={llm_summary['call_count']}",
                    f"llm_total_ms={llm_summary['total_duration_ms']}",
                    f"llm_max_ms={llm_summary['max_duration_ms']}",
                )
            )
        )
        lines.append(
            " ".join(
                (
                    f"llm_health_degraded={llm_health['degraded']}",
                    f"llm_error_calls={llm_health['error_call_count']}",
                    f"llm_provider_error_calls={llm_health['provider_error_call_count']}",
                    f"llm_validation_error_calls={llm_health['validation_error_call_count']}",
                    f"llm_error_types={llm_health['error_types']}",
                    f"llm_http_statuses={llm_health['http_statuses']}",
                )
            )
        )
        for call in result.slowest_llm_calls[:3]:
            call_parts = [
                f"- slow_llm={call.get('event_name', 'unknown')}",
                f"scenario={call.get('scenario_id', 'unknown')}",
                f"duration_ms={call.get('duration_ms')}",
                f"prompt_tokens={call.get('prompt_token_estimate')}",
                f"output_tokens={call.get('output_token_estimate')}",
            ]
            if "output_compact_token_estimate" in call and call.get("output_compact_token_estimate") != call.get(
                "output_token_estimate"
            ):
                call_parts.append(f"compact_output_tokens={call.get('output_compact_token_estimate')}")
            if "output_expanded_token_estimate" in call:
                call_parts.append(f"expanded_output_tokens={call.get('output_expanded_token_estimate')}")
            if "output_default_overhead_chars" in call:
                call_parts.append(f"default_overhead_chars={call.get('output_default_overhead_chars')}")
            lines.append(" ".join(call_parts))
    if result.llm_audit_summary:
        lines.append("LLM call-budget audit:")
        for group in result.llm_audit_summary[:5]:
            lines.append(
                " ".join(
                    (
                        f"- route={group['route_signature']}",
                        f"chain={','.join(group['event_chain']) or 'none'}",
                        f"turns={group['turn_count']}",
                        f"calls={group['call_count']}",
                        f"p50_ms={group['llm_total_ms_p50']}",
                        f"p95_ms={group['llm_total_ms_p95']}",
                        f"provider_cache_hit_rate={group['provider_cache_hit_rate']}",
                        f"budgets={group['budget_statuses']}",
                    )
                )
            )
    if result.route_latency_summary:
        lines.append("Route latency measurement:")
        for route in result.route_latency_summary[:10]:
            lines.append(
                " ".join(
                    (
                        f"- route={route['route_signature']}",
                        f"turns={route['turn_count']}",
                        f"first_visible_p95_ms={route['first_visible_ms_p95']}",
                        f"final_ready_p95_ms={route['final_ready_ms_p95']}",
                        f"completion_p95_ms={route['completion_ms_p95']}",
                        f"outside_graph_p95_ms={route['outside_graph_ms_p95']}",
                        f"heartbeat_turns={route['heartbeat_turn_count']}",
                    )
                )
            )
    if result.llm_audit_candidates:
        lines.append("LLM audit candidates:")
        for candidate in result.llm_audit_candidates[:5]:
            lines.append(
                " ".join(
                    (
                        f"- scenario={candidate['scenario_id']}#{candidate['turn_index']}",
                        f"route={candidate['route_signature']}",
                        f"chain={','.join(candidate['event_chain']) or 'none'}",
                        f"calls={candidate['llm_call_count']}",
                        f"llm_total_ms={candidate['llm_total_ms']}",
                        f"budget={candidate['budget_status']}",
                    )
                )
            )
    if result.failed_turns:
        for turn in result.failed_turns:
            lines.append(f"- {turn.scenario_id} #{turn.turn_index} {turn.user_text!r}: {'; '.join(turn.errors)}")
    return "\n".join(lines) + "\n"


def print_readiness_report(result: ReadinessRunResult) -> None:
    print(format_readiness_report(result), end="")
