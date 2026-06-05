"""Replay-set parity harness for semantic-router prompt coverage."""

from __future__ import annotations

import json
from pathlib import Path

from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_semantic_router_prompts import (
    SEMANTIC_ROUTER_SYSTEM_PROMPT,
)

_CASES_PATH = Path("tests/fixtures/semantic_router_replay_cases.json")


def _load_replay_cases() -> list[dict[str, str]]:
    return json.loads(_CASES_PATH.read_text(encoding="utf-8"))


def test_semantic_router_replay_case_set_covers_query_first_and_guardrails() -> None:
    cases = _load_replay_cases()

    assert cases, "Semantic-router replay case set must not be empty"
    case_ids = {case["id"] for case in cases}
    assert "query_income_en" in case_ids
    assert "query_income_pidgin" in case_ids
    assert "query_credits_yoruba" in case_ids
    assert "query_income_hausa" in case_ids
    assert "query_income_igbo" in case_ids
    assert "query_credits_french" in case_ids
    assert "query_followup_pending_last_3_days" in case_ids
    assert "query_followup_total_en" in case_ids
    assert "query_followup_total_pidgin" in case_ids
    assert "query_followup_total_yoruba" in case_ids
    assert "account_balance_guardrail" in case_ids
    assert "transfer_single_direct" in case_ids
    assert "mixed_transfer_batch" in case_ids
    assert "mixed_cross_domain" in case_ids
    assert "mixed_cross_domain_executor_boundary" in case_ids
    assert "airtime_multi_phone_batch" in case_ids


def test_semantic_router_prompt_covers_replay_case_lines() -> None:
    for case in _load_replay_cases():
        assert case["prompt_line"] in SEMANTIC_ROUTER_SYSTEM_PROMPT, case["id"]
