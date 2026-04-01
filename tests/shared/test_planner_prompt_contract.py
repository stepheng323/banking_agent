"""Contract tests for planner prompt guidance."""

from typing import get_args

import tiktoken

from shared.services.task_planner import (
    INTERRUPT_ROUTER_SYSTEM_PROMPT,
    PLANNER_PROMPT_BASELINE_RESULT,
    SEMANTIC_ROUTER_SYSTEM_PROMPT,
    PlannerPromptBuildInput,
    PlannerPromptSignals,
    build_planner_system_prompt,
)
from shared.types.planner import ContextReadSubtype


def _build_prompt(
    text: str,
    context: str,
    signals: PlannerPromptSignals | None = None,
) -> tuple[str, str, tuple[str, ...]]:
    result = build_planner_system_prompt(
        PlannerPromptBuildInput(text=text, context=context, signals=signals or PlannerPromptSignals())
    )
    return result.system_prompt, result.profile, result.selected_bundle_ids


def test_beneficiary_reactive_save_requires_explicit_intent() -> None:
    """Prompt should prevent greeting text from being treated as save consent."""
    runtime_prompt, _, bundles = _build_prompt(
        "Save as Gaines",
        "Asked to save beneficiary",
        PlannerPromptSignals(has_beneficiary_suggestion=True),
    )
    assert "context" in bundles
    assert "R12_BENEFICIARY_HANDLING" in runtime_prompt
    assert 'Save-beneficiary prompt + "Hi" -> conversational' in runtime_prompt


def test_context_read_fastpath_rules_removed_from_planner_prompt() -> None:
    """Planner prompt should no longer spend budget on router-owned context fastpath rules."""
    runtime_prompt, _, _ = _build_prompt("Which account is default?", "User State has default account")
    assert "R16_FASTPATH_CONTEXT_READ" not in runtime_prompt
    assert "R17_FASTPATH_FALLBACK" not in runtime_prompt
    assert "R18_FASTPATH_SUBTYPE" not in runtime_prompt
    fastpath_values = set(get_args(ContextReadSubtype))
    assert "account_mandate_readiness_summary" in fastpath_values
    assert "account_linked_bank_existence_check" in fastpath_values
    assert "beneficiary_name_match_preview" in fastpath_values
    assert "flow_recap" in fastpath_values
    assert "flow_missing_requirements" in fastpath_values


def test_interrupt_status_query_contract_present() -> None:
    """Interrupt router prompt should include status-query decision + subtype contract."""
    assert (
        "decision: continue_flow | switch_intent | cancel | unclear | approve_flow | reject_flow | status_query"
        in INTERRUPT_ROUTER_SYSTEM_PROMPT
    )
    assert "status_query_type: recap | requirements | null" in INTERRUPT_ROUTER_SYSTEM_PROMPT
    assert "decision=status_query" in INTERRUPT_ROUTER_SYSTEM_PROMPT
    assert '"make it 20k"' in INTERRUPT_ROUTER_SYSTEM_PROMPT
    assert '"split 20k 70/30 btw mum and gaines"' in INTERRUPT_ROUTER_SYSTEM_PROMPT
    assert "target_intent=null" in INTERRUPT_ROUTER_SYSTEM_PROMPT


def test_transfer_recipient_fidelity_rules_present() -> None:
    """Prompt must preserve typed transfer recipient names for resolver disambiguation."""
    runtime_prompt, _, bundles = _build_prompt(
        "Send 5k to tolu",
        "None",
        PlannerPromptSignals(active_flow_type="transfer"),
    )
    assert "money_move" in bundles
    assert "R19_TRANSFER_FIDELITY" in runtime_prompt
    assert "send_money amount=8000" in runtime_prompt
    assert "recipient omitted" in runtime_prompt


def test_multilingual_safety_rules_present() -> None:
    """Prompt should state language-agnostic routing and disambiguation boundaries."""
    runtime_prompt, _, _ = _build_prompt("How far", "None")
    assert "R23_MULTILINGUAL_SAFETY" in runtime_prompt


def test_follow_up_referent_binding_rules_present() -> None:
    """Prompt should anchor vague follow-ups to the most recent discussed domain."""
    runtime_prompt, _, bundles = _build_prompt(
        "Send 10k to her",
        "Recent Chat last turn was beneficiary_count answer",
        PlannerPromptSignals(recent_domain_focus="query", active_flow_type="query"),
    )
    assert "R14_REFERENCE_BINDING" in runtime_prompt
    assert "R14:pronoun|index->selector_ref" in runtime_prompt
    assert "TARGETED EXAMPLES (QUERY)" not in runtime_prompt
    assert 'Active transfer flow + "Where did we stop?" -> flow_recap.' not in runtime_prompt
    assert "query" not in bundles


def test_transfer_pronoun_reference_continuity_rules_present() -> None:
    """Prompt should preserve beneficiary pronoun continuity with reference semantics."""
    runtime_prompt, _, _ = _build_prompt(
        "Send 10k to her",
        "Recent Chat last turn listed beneficiaries",
        PlannerPromptSignals(active_flow_type="transfer"),
    )
    assert "R14_REFERENCE_BINDING" in runtime_prompt
    assert '{"selector":"previous"}' in runtime_prompt
    assert '{"selector":"index","index":N}' in runtime_prompt


def test_transfer_scheduling_rules_present() -> None:
    """Prompt should include schedule/recurring transfer action contracts."""
    runtime_prompt, _, _ = _build_prompt(
        "Send 10k to Mum tomorrow 9am",
        "None",
        PlannerPromptSignals(active_flow_type="transfer"),
    )
    assert "R21_TRANSFER_SCHEDULING" in runtime_prompt
    assert "schedule_transfer" in runtime_prompt
    assert "recurring_transfer" in runtime_prompt
    assert "list_scheduled_transfers" in runtime_prompt
    assert "cancel_scheduled_transfer" in runtime_prompt


def test_money_move_one_shot_multilingual_examples_present() -> None:
    """Prompt should include compact one-shot extraction examples across supported languages."""
    runtime_prompt, _, bundles = _build_prompt(
        "Send 20k to 0760505261 First Bank",
        "None",
        PlannerPromptSignals(has_transaction_intent_hint=True),
    )
    assert "money_move" in bundles
    assert "R26_ONE_SHOT_COMPLETENESS" in runtime_prompt
    assert "R27_RECIPIENT_SPLIT" in runtime_prompt
    assert "one transfer task" in runtime_prompt
    assert "recipient_allocations" in runtime_prompt
    assert "Send 20k to 0760505261 First Bank" in runtime_prompt
    assert "Split 20k between Mum and Gaines" in runtime_prompt
    assert "Send 10k each to Mum, Tolu and Doyin" in runtime_prompt
    assert "Send 20k 70/30 btw Mum and Gaines" in runtime_prompt
    assert "explicit_split={Access:10000,GTB:10000}" in runtime_prompt
    assert "Abeg buy 2k airtime for 08031234567 mtn" in runtime_prompt
    assert "Jowo ra data 1gb fun 08031234567 mtn" in runtime_prompt
    assert "Don Allah tura 5k zuwa 0760505261 First Bank" in runtime_prompt
    assert "Biko buy 3k airtime for my line mtn" in runtime_prompt
    assert "Buy 200 airtime for 08031234567, 08067892221, 08033038674" in runtime_prompt
    assert "Envoie 5k a 0760505261 First Bank" in runtime_prompt


def test_transfer_only_prompt_bundle_selected_for_guardrail_transfer_handoff() -> None:
    runtime_prompt, profile, bundles = _build_prompt(
        "okay send 10k each to mum, tolu and doyin",
        "Recent user state summary",
        PlannerPromptSignals(
            forced_domain_owner="transfer",
            expected_transaction_executors=("transfer",),
        ),
    )
    assert "transfer_only" in bundles
    assert "money_move" not in bundles
    assert "executor_coverage_guard" not in bundles
    assert "TARGETED EXAMPLES (TRANSFER_ONLY)" in runtime_prompt
    assert "TARGETED EXAMPLES (MONEY_MOVE)" not in runtime_prompt
    assert "EXECUTOR COVERAGE GUARD" not in runtime_prompt
    assert "ex_transfer_only" in profile


def test_mixed_money_move_coverage_rules_present() -> None:
    """Prompt should force full task coverage for explicit mixed money-move requests."""
    runtime_prompt, _, bundles = _build_prompt(
        "Send 10k to Mum and buy me 5k airtime",
        "None",
        PlannerPromptSignals(expected_transaction_executors=("transfer", "airtime")),
    )
    assert "money_move" in bundles
    assert "executor_coverage_guard" in bundles
    assert "R22_MIXED_MONEY_MOVE" in runtime_prompt
    assert "TARGETED EXAMPLES (MONEY_MOVE)" in runtime_prompt
    assert "send_money" in runtime_prompt
    assert "buy_airtime" in runtime_prompt
    assert "EXECUTOR COVERAGE GUARD" in runtime_prompt


def test_transfer_only_prompt_bundle_excludes_executor_coverage_guard() -> None:
    runtime_prompt, _, bundles = _build_prompt(
        "send half my zenith to mum",
        "None",
        PlannerPromptSignals(
            forced_domain_owner="transfer",
            expected_transaction_executors=("transfer",),
        ),
    )
    assert "transfer_only" in bundles
    assert "executor_coverage_guard" not in bundles
    assert "EXECUTOR COVERAGE GUARD" not in runtime_prompt


def test_semantic_router_expected_executor_coverage_rules_present() -> None:
    """Semantic-router prompt should require all explicit mixed transaction executors."""
    assert "direct_context_answer" in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "domain_query" in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "domain_account" in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "planner_mixed" in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "expected_transaction_executors" in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "explicit mixed transaction requests" in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "include every mentioned executor" in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '["transfer","airtime"]' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "Do NOT add executors for non-transaction clauses inside a mixed request." in (
        SEMANTIC_ROUTER_SYSTEM_PROMPT
    )
    assert (
        '"send 10k to mum and show my last 3 credits" -> ["transfer"]' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    )
    assert 'Do not infer data executor from words like "credit", "transaction data"' in (
        SEMANTIC_ROUTER_SYSTEM_PROMPT
    )
    assert '"Can I use First Bank now?" -> direct_context_answer' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"Is First Bank ready?" -> direct_context_answer' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"Is my First Bank account ready?" -> direct_context_answer' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"Show my linked accounts" -> domain_account' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"Can I use fisr bank now?" -> direct_context_answer' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"Do I still have Mum saved?" -> direct_context_answer' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"What\'s my income this month" -> domain_query' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"Where did we stop?" -> direct_context_answer' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"What are we doing again?" -> direct_context_answer' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"Show my last transaction" -> domain_query' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"Show my beneficiaries" -> domain_beneficiary' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"Send 5k to Mum" -> domain_transfer' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"Buy 2k airtime for 08031234567" -> domain_airtime' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"Buy 1gb for me" -> domain_data' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"Send 10k to Mum and 5k to Gaines" -> planner_mixed' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"Split 20k between Mum and Dad" -> planner_mixed' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"Buy airtime and tell me my balance" -> planner_mixed' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"Buy 200 airtime for 08031234567, 08067892221, 08033038674" -> planner_mixed' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"More" while viewing transactions -> domain_query with mode=continuation' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"send 10k to mum and show my last 3 credits" -> planner_mixed' in SEMANTIC_ROUTER_SYSTEM_PROMPT


def test_semantic_router_language_switch_contract_present() -> None:
    """Semantic-router contract must expose explicit language-switch request capture."""
    assert "requested_language: English | Pidgin | Yoruba | Hausa | Igbo | null" in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "If user asks to switch language (for example, \"Can you switch to Pidgin?\", \"speak Yoruba now\"), set" in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"Can you switch to Pidgin?"' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"speak Yoruba now"' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "requested_language to the requested locale" in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "Do not apply cancellation/flow-guess logic for this request." in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "decision=direct_reply" in SEMANTIC_ROUTER_SYSTEM_PROMPT


def test_semantic_router_pending_query_clarification_contract_present() -> None:
    """Semantic-router prompt should route pending clarification answers back to query."""
    assert "If the context shows a pending query clarification" in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert 'pending query clarification + "last 3 days" -> domain_query with mode=continuation' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert 'pending query clarification + "this month" -> domain_query with mode=continuation' in SEMANTIC_ROUTER_SYSTEM_PROMPT


def test_semantic_router_multilingual_query_examples_present() -> None:
    """Semantic-router prompt should anchor multilingual query-first examples."""
    assert '"Wetin be my income this month" -> domain_query' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"Fihan mi awon credit transactions mi fun osu yi" -> domain_query' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"Nawa na karba a wannan watan" -> domain_query' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"Ego ole ka m natara n\'onwa a" -> domain_query' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"Montre mes transactions credit de ce mois" -> domain_query' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"wetin be total" after a transaction list -> domain_query with mode=continuation' in (
        SEMANTIC_ROUTER_SYSTEM_PROMPT
    )
    assert '"lapapo meloo" after a transaction list -> domain_query with mode=continuation' in (
        SEMANTIC_ROUTER_SYSTEM_PROMPT
    )


def test_runtime_planner_prompt_is_compact_for_generic_turns() -> None:
    """Runtime prompt should remain minimal for simple turns."""
    runtime_prompt, profile, bundles = _build_prompt("hello", "None", PlannerPromptSignals())
    expanded_prompt, _, expanded_bundles = _build_prompt(
        "Send 10k to Mum and buy 5k airtime and show transactions",
        "Active Query Session. Asked to save beneficiary. Recent Chat.",
        PlannerPromptSignals(
            active_flow_type="transfer",
            query_session_active=True,
            recent_domain_focus="query",
            has_beneficiary_suggestion=True,
            expected_transaction_executors=("transfer", "airtime"),
        ),
    )
    assert "## RULE IDS" in runtime_prompt
    assert "TARGETED EXAMPLES (COMMON)" in runtime_prompt
    assert "TARGETED EXAMPLES (MONEY_MOVE)" not in runtime_prompt
    assert "TARGETED EXAMPLES (QUERY)" not in runtime_prompt
    assert "TARGETED EXAMPLES (CONTEXT)" not in runtime_prompt
    assert "schema" in profile
    assert "ex_common" in profile
    assert not bundles
    assert set(expanded_bundles) == {"money_move", "context", "executor_coverage_guard"}
    assert len(runtime_prompt) <= PLANNER_PROMPT_BASELINE_RESULT.char_count
    assert len(runtime_prompt) < len(expanded_prompt)


def test_runtime_planner_prompt_size_budget_targets() -> None:
    """Runtime prompt stays within agreed size ceilings after optimization."""
    generic_prompt, _, _ = _build_prompt("hello", "None", PlannerPromptSignals())
    expanded_prompt, _, _ = _build_prompt(
        "Send 10k to Mum and buy 5k airtime and show transactions",
        "Active Query Session. Asked to save beneficiary. Recent Chat.",
        PlannerPromptSignals(
            active_flow_type="transfer",
            query_session_active=True,
            recent_domain_focus="query",
            has_beneficiary_suggestion=True,
            expected_transaction_executors=("transfer", "airtime"),
        ),
    )
    assert len(generic_prompt) <= 1600
    assert len(expanded_prompt) <= 4350
    encoding = tiktoken.get_encoding("o200k_base")
    assert len(encoding.encode(generic_prompt)) <= 390
    assert len(encoding.encode(expanded_prompt)) <= 1130


def test_runtime_planner_prompt_adds_money_move_examples_when_relevant() -> None:
    """Runtime prompt should include money-move examples for transaction turns."""
    runtime_prompt, profile, bundles = _build_prompt(
        "Send 10k to mum and buy me 5k airtime",
        "None",
        PlannerPromptSignals(active_flow_type="transfer"),
    )
    assert "money_move" in bundles
    assert "TARGETED EXAMPLES (MONEY_MOVE)" in runtime_prompt
    assert "ex_money_move" in profile
    assert "R09_CONTEXT_OVERRIDE" in runtime_prompt


def test_runtime_planner_prompt_omits_query_examples_when_query_signals_present() -> None:
    """Planner prompt should stay lean even if query-related signals are present."""
    runtime_prompt, profile, bundles = _build_prompt(
        "How much did I spend last week?",
        "None",
        PlannerPromptSignals(query_session_active=True, recent_domain_focus="query"),
    )
    assert "query" not in bundles
    assert "TARGETED EXAMPLES (QUERY)" not in runtime_prompt
    assert "R13_QUERY_CONTINUATION" not in runtime_prompt
    assert "ex_query" not in profile


def test_runtime_planner_prompt_adds_context_examples_when_contextful() -> None:
    """Runtime prompt should include context examples when planner context indicates active flow memory."""
    runtime_prompt, profile, bundles = _build_prompt(
        "List them",
        "Recent Chat last turn was account_count answer",
        PlannerPromptSignals(active_flow_type="account", has_beneficiary_suggestion=True),
    )
    assert "context" in bundles
    assert "TARGETED EXAMPLES (CONTEXT)" in runtime_prompt
    assert "R15_RESUMPTION" in runtime_prompt
    assert "ex_context" in profile
    assert 'Active transfer flow + "send it to her" -> send_money with selector reference.' in runtime_prompt
