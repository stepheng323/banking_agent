"""Replay-set semantic parity harness for compact planner prompts."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner import PLANNER_USER_PROMPT_TEMPLATE
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_prompt_models import (
    PlannerPromptBuildInput,
    PlannerPromptSignals,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_prompt_runtime import (
    build_runtime_planner_system_prompt,
)
from shared.types.planner import PlannerOutput

_CASES_PATH = Path("tests/fixtures/planner_replay_cases.json")
_MULTILINGUAL_GREETING_CASE_IDS = (
    "greeting_pidgin",
    "greeting_yoruba",
    "greeting_hausa",
    "greeting_igbo",
)
_MULTILINGUAL_TRANSFER_EQUIVALENT_CASE_IDS = (
    "transfer_missing_slots",
    "transfer_missing_slots_pidgin",
)


def _load_replay_cases() -> list[dict[str, Any]]:
    return json.loads(_CASES_PATH.read_text(encoding="utf-8"))


def _build_signals(payload: dict[str, Any]) -> PlannerPromptSignals:
    return PlannerPromptSignals(
        active_flow_type=payload.get("active_flow_type"),
        pending_interrupt_kind=payload.get("pending_interrupt_kind"),
        query_session_active=bool(payload.get("query_session_active", False)),
        query_session_source=payload.get("query_session_source"),
        recent_domain_focus=payload.get("recent_domain_focus"),
        has_beneficiary_suggestion=bool(payload.get("has_beneficiary_suggestion", False)),
        has_user_state_summary=bool(payload.get("has_user_state_summary", False)),
        has_short_term_memory=bool(payload.get("has_short_term_memory", False)),
        has_quote=bool(payload.get("has_quote", False)),
        has_transaction_intent_hint=bool(payload.get("has_transaction_intent_hint", False)),
        expected_transaction_executors=tuple(payload.get("expected_transaction_executors", [])),
    )


def _critical_signature(output: PlannerOutput) -> dict[str, Any]:
    def _normalize_text(value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip().strip("[]{}(),.:;\"' ")

    tasks: list[dict[str, Any]] = []
    executors: list[str] = []
    for task in output.tasks:
        executors.append(task.executor)
        ref = task.parameters.reference
        normalized_recipient = _normalize_text(task.parameters.recipient)
        normalized_recipient_name = _normalize_text(task.parameters.recipient_name) or normalized_recipient
        task_sig = {
            "executor": task.executor,
            "action": task.action,
            "depends_on": list(task.depends_on),
            "amount": task.parameters.amount,
            "recipient": normalized_recipient,
            "recipient_name": normalized_recipient_name,
            "recipient_phone": task.parameters.recipient_phone,
            "phone": task.parameters.phone,
            "network": task.parameters.network,
            "bank_name": _normalize_text(task.parameters.bank_name),
            "recipient_account": task.parameters.recipient_account,
            "plan": task.parameters.plan,
            "schedule": task.parameters.schedule,
            "recipient_allocations": [
                {
                    "recipient_name": _normalize_text(item.recipient_name),
                    "amount": item.amount,
                }
                for item in (task.parameters.recipient_allocations or [])
            ],
            "reference": (
                None
                if ref is None
                else {
                    "selector": ref.selector,
                    "index": ref.index,
                    "label": ref.label,
                }
            ),
        }
        tasks.append(task_sig)

    normalized_intent = output.primary_intent
    if executors:
        normalized_intent = executors[0] if len(set(executors)) == 1 else "mixed"
    return {
        "primary_intent": normalized_intent,
        "beneficiary_route": output.beneficiary_route,
        "is_cancellation": output.is_cancellation,
        "task_count": len(tasks),
        "tasks": tasks,
    }


def _as_number(value: str | float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, float):
        return value
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _assert_case_signature(case_id: str, signature: dict[str, Any]) -> None:
    tasks = signature["tasks"]
    first_task = tasks[0] if tasks else None

    if case_id in {
        "greeting_pidgin",
        "greeting_yoruba",
        "greeting_hausa",
        "greeting_igbo",
        "beneficiary_save_greeting_guard",
    }:
        assert signature["primary_intent"] == "conversational", case_id
        assert signature["task_count"] == 0, case_id
        return

    if case_id == "beneficiary_list_saved":
        assert signature["beneficiary_route"] == "beneficiary_list", case_id
        if signature["task_count"] > 0:
            assert first_task is not None and first_task["executor"] == "beneficiary", case_id
            assert first_task["action"] == "list_beneficiaries", case_id
        return

    if case_id == "beneficiary_top_recipients":
        assert signature["beneficiary_route"] == "recipient_ranking", case_id
        assert signature["task_count"] >= 1, case_id
        assert first_task is not None and first_task["executor"] == "query", case_id
        assert first_task["action"] == "beneficiary_summary", case_id
        return

    if case_id == "beneficiary_save_after_suggestion":
        assert signature["beneficiary_route"] == "none", case_id
        assert signature["task_count"] >= 1, case_id
        assert first_task is not None and first_task["executor"] == "beneficiary", case_id
        assert first_task["action"] == "save_beneficiary", case_id
        return

    if case_id in {"transfer_missing_slots", "transfer_missing_slots_pidgin"}:
        assert signature["primary_intent"] in {"transfer", "mixed"}, case_id
        assert signature["task_count"] >= 1, case_id
        assert first_task is not None and first_task["executor"] == "transfer", case_id
        assert first_task["action"] == "send_money", case_id
        amount = _as_number(first_task["amount"])
        assert amount == 8000, case_id
        return

    if case_id.startswith("oneshot_transfer_"):
        assert signature["primary_intent"] in {"transfer", "mixed"}, case_id
        assert signature["task_count"] >= 1, case_id
        assert first_task is not None and first_task["executor"] == "transfer", case_id
        assert first_task["action"] == "send_money", case_id
        assert _as_number(first_task["amount"]) is not None, case_id
        assert first_task["recipient_account"], case_id
        assert first_task["bank_name"], case_id
        return

    if case_id.startswith("oneshot_airtime_"):
        assert signature["primary_intent"] in {"airtime", "mixed"}, case_id
        assert signature["task_count"] >= 1, case_id
        assert first_task is not None and first_task["executor"] == "airtime", case_id
        assert first_task["action"] == "buy_airtime", case_id
        assert _as_number(first_task["amount"]) is not None, case_id
        assert first_task["recipient_phone"] or first_task["phone"], case_id
        assert first_task["network"], case_id
        return

    if case_id.startswith("oneshot_data_"):
        assert signature["primary_intent"] in {"data", "mixed"}, case_id
        assert signature["task_count"] >= 1, case_id
        assert first_task is not None and first_task["executor"] == "data", case_id
        assert first_task["action"] == "buy_data", case_id
        assert first_task["recipient_phone"] or first_task["phone"], case_id
        assert first_task["network"], case_id
        assert first_task["plan"] or _as_number(first_task["amount"]) is not None, case_id
        return

    if case_id == "mixed_transfer_airtime":
        assert signature["primary_intent"] == "mixed", case_id
        assert signature["task_count"] >= 2, case_id
        executors = [task["executor"] for task in tasks]
        actions = [task["action"] for task in tasks]
        assert executors[0] == "transfer", case_id
        assert "airtime" in executors, case_id
        assert "send_money" in actions and "buy_airtime" in actions, case_id
        return

    if case_id == "recipient_split_three_way":
        assert signature["primary_intent"] in {"transfer", "mixed"}, case_id
        assert signature["task_count"] >= 1, case_id
        assert first_task is not None and first_task["executor"] == "transfer", case_id
        allocations = first_task["recipient_allocations"]
        assert len(allocations) == 3, case_id
        assert [item["recipient_name"] for item in allocations] == ["Mum", "Tolu", "Doyin"], case_id
        assert all(_as_number(item["amount"]) == 10000 for item in allocations), case_id
        return

    if case_id == "query_continuation_any_credits":
        assert signature["primary_intent"] == "query", case_id
        assert signature["task_count"] >= 1, case_id
        assert first_task is not None and first_task["executor"] == "query", case_id
        assert first_task["action"] in {"transaction_search", "transaction_list"}, case_id
        return

    if case_id == "query_send_again_replay":
        assert signature["primary_intent"] in {"transfer", "mixed"}, case_id
        assert signature["task_count"] >= 1, case_id
        assert first_task is not None and first_task["executor"] == "transfer", case_id
        assert first_task["action"] == "send_money", case_id
        return

    if case_id == "context_list_them_accounts":
        assert signature["primary_intent"] == "account", case_id
        assert signature["task_count"] >= 1, case_id
        assert first_task is not None and first_task["executor"] == "account", case_id
        assert first_task["action"] == "list_accounts", case_id
        return

    if case_id == "schedule_transfer":
        assert signature["primary_intent"] in {"transfer", "mixed"}, case_id
        assert signature["task_count"] >= 1, case_id
        assert first_task is not None and first_task["executor"] == "transfer", case_id
        assert first_task["action"] in {"schedule_transfer", "send_money"}, case_id
        return

    raise AssertionError(f"Unhandled replay case: {case_id}")


def test_replay_case_set_covers_core_planner_shapes() -> None:
    cases = _load_replay_cases()

    assert cases, "Replay case set must not be empty"
    case_ids = {case["id"] for case in cases}
    assert "mixed_transfer_airtime" in case_ids
    assert "recipient_split_three_way" in case_ids
    assert "query_continuation_any_credits" in case_ids
    assert "context_list_them_accounts" in case_ids
    assert "greeting_yoruba" in case_ids
    assert "greeting_hausa" in case_ids
    assert "greeting_igbo" in case_ids
    assert "transfer_missing_slots_pidgin" in case_ids
    assert "beneficiary_list_saved" in case_ids
    assert "beneficiary_top_recipients" in case_ids
    assert "beneficiary_save_after_suggestion" in case_ids
    for prefix in ("oneshot_transfer_", "oneshot_airtime_", "oneshot_data_"):
        for locale in ("en", "pidgin", "yoruba", "hausa", "igbo", "french"):
            assert f"{prefix}{locale}" in case_ids


def test_compact_prompt_remains_bundle_driven() -> None:
    case = {
        "text": "Send 10k to Mum and buy 5k airtime and show transactions",
        "context": "Active Query Session. Asked to save beneficiary. Recent Chat.",
        "signals": {
            "active_flow_type": "transfer",
            "query_session_active": True,
            "recent_domain_focus": "query",
            "has_beneficiary_suggestion": True,
            "expected_transaction_executors": ["transfer", "airtime"],
        },
    }
    prompt_input = PlannerPromptBuildInput(
        text=case["text"],
        context=case["context"],
        signals=_build_signals(case["signals"]),
    )

    compact = build_runtime_planner_system_prompt(prompt_input)
    assert compact.selected_bundle_ids == ("money_move", "context", "executor_coverage_guard")
    assert "TARGETED EXAMPLES (MONEY_MOVE)" in compact.system_prompt
    assert "TARGETED EXAMPLES (CONTEXT)" in compact.system_prompt
    assert "TARGETED EXAMPLES (QUERY)" not in compact.system_prompt


@pytest.mark.asyncio
async def test_replay_parity_live_model() -> None:
    """Optional live semantic gate for compact planner prompt.

    Enabled only when both env vars are set:
    - PLANNER_REPLAY_PARITY_LIVE=1
    - OPENAI_API_KEY
    """

    if os.getenv("PLANNER_REPLAY_PARITY_LIVE") != "1":
        pytest.skip("set PLANNER_REPLAY_PARITY_LIVE=1 to run live parity")
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY is required for live parity")
    chat_openai = pytest.importorskip("langchain_openai")

    model_name = os.getenv("PLANNER_REPLAY_PARITY_MODEL", "gpt-4o-mini")
    planner_llm = chat_openai.ChatOpenAI(model=model_name, temperature=0, seed=42)
    structured = planner_llm.with_structured_output(PlannerOutput)

    replay_cases = _load_replay_cases()
    compact_signatures: dict[str, dict[str, Any]] = {}

    for case in replay_cases:
        signals = _build_signals(case.get("signals", {}))
        prompt_input = PlannerPromptBuildInput(text=case["text"], context=case["context"], signals=signals)

        compact_prompt = build_runtime_planner_system_prompt(prompt_input).system_prompt
        user_prompt = PLANNER_USER_PROMPT_TEMPLATE.format(
            phone_number="2348011111111",
            user_message=case["text"],
            context=case["context"],
        )

        compact_output = await structured.ainvoke(
            [
                {"role": "system", "content": compact_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )

        assert isinstance(compact_output, PlannerOutput)

        compact_sig = _critical_signature(compact_output)
        compact_signatures[case["id"]] = compact_sig
        _assert_case_signature(case["id"], compact_sig)

    # Multilingual semantic guardrails: equivalent asks should preserve extraction behavior.
    greeting_signatures = [compact_signatures[case_id] for case_id in _MULTILINGUAL_GREETING_CASE_IDS]
    for case_id, signature in zip(_MULTILINGUAL_GREETING_CASE_IDS, greeting_signatures, strict=True):
        assert signature["primary_intent"] == "conversational", case_id
        assert signature["task_count"] == 0, case_id
    assert all(signature == greeting_signatures[0] for signature in greeting_signatures[1:])

    transfer_signatures = [compact_signatures[case_id] for case_id in _MULTILINGUAL_TRANSFER_EQUIVALENT_CASE_IDS]
    for case_id, signature in zip(_MULTILINGUAL_TRANSFER_EQUIVALENT_CASE_IDS, transfer_signatures, strict=True):
        assert signature["primary_intent"] in {"transfer", "mixed"}, case_id
        assert signature["task_count"] >= 1, case_id
        assert signature["tasks"][0]["executor"] == "transfer", case_id
    assert transfer_signatures[0] == transfer_signatures[1]
