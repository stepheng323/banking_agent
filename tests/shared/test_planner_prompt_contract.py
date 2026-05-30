"""Contract tests for planner prompt guidance."""

from typing import get_args

import tiktoken

from apps.chat.src.agent.orchestrator.planning.task_planner_interrupt_prompts import (
    INTERRUPT_ROUTER_SYSTEM_PROMPT_COMPACT,
    INTERRUPT_ROUTER_SYSTEM_PROMPT_FULL,
    PENDING_ACTION_EDIT_SYSTEM_PROMPT,
)
from apps.chat.src.agent.orchestrator.planning.task_planner_prompt_models import (
    PlannerPromptBuildInput,
    PlannerPromptSignals,
)
from apps.chat.src.agent.orchestrator.planning.task_planner_prompt_runtime import (
    PLANNER_PROMPT_BASELINE_RESULT,
    build_runtime_planner_system_prompt,
)
from apps.chat.src.agent.orchestrator.planning.task_planner_semantic_router_prompts import SEMANTIC_ROUTER_SYSTEM_PROMPT
from shared.types.planner import ContextReadSubtype


def _build_prompt(
    text: str,
    context: str,
    signals: PlannerPromptSignals | None = None,
) -> tuple[str, str, tuple[str, ...]]:
    result = build_runtime_planner_system_prompt(
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
        in INTERRUPT_ROUTER_SYSTEM_PROMPT_FULL
    )
    assert "status_query_type: recap | requirements | null" in INTERRUPT_ROUTER_SYSTEM_PROMPT_FULL
    assert "decision=status_query" in INTERRUPT_ROUTER_SYSTEM_PROMPT_FULL
    assert '"make it 20k"' in INTERRUPT_ROUTER_SYSTEM_PROMPT_FULL
    assert '"split 20k 70/30 btw mum and gaines"' in INTERRUPT_ROUTER_SYSTEM_PROMPT_FULL
    assert "target_intent=null" in INTERRUPT_ROUTER_SYSTEM_PROMPT_FULL


def test_pending_action_edit_contract_present() -> None:
    """Pending edit prompt should classify edits without authorizing money movement."""
    assert (
        "operation: remove_tasks | restore_tasks | update_fields | add_tasks | approve_flow | cancel_all |"
        in PENDING_ACTION_EDIT_SYSTEM_PROMPT
    )
    assert "target_task_ids" in PENDING_ACTION_EDIT_SYSTEM_PROMPT
    assert "target_types: transfer | airtime | data" in PENDING_ACTION_EDIT_SYSTEM_PROMPT
    assert "deterministic code will re-render confirmation and require PIN" in PENDING_ACTION_EDIT_SYSTEM_PROMPT
    assert "which account/bank to pay from" in PENDING_ACTION_EDIT_SYSTEM_PROMPT
    assert "default-account update while a confirmation is pending" in PENDING_ACTION_EDIT_SYSTEM_PROMPT


def test_interrupt_compact_prompt_is_shorter_but_keeps_core_contract() -> None:
    assert len(INTERRUPT_ROUTER_SYSTEM_PROMPT_COMPACT) < len(INTERRUPT_ROUTER_SYSTEM_PROMPT_FULL)
    assert "decision: continue_flow | switch_intent | cancel | unclear | approve_flow | reject_flow | status_query" in (
        INTERRUPT_ROUTER_SYSTEM_PROMPT_COMPACT
    )
    assert "status_query_type: recap | requirements | null" in INTERRUPT_ROUTER_SYSTEM_PROMPT_COMPACT
    assert "Balance/account-status asks map to target_intent=account." in INTERRUPT_ROUTER_SYSTEM_PROMPT_COMPACT


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


def test_recent_surface_memory_enables_context_followup_rules() -> None:
    runtime_prompt, _, bundles = _build_prompt(
        "Is that all?",
        "RECENT_CONTEXT:\n- beneficiary_list: [1] Tolu Access (Access), [2] Tolu GTB (GTBank)",
        PlannerPromptSignals(has_short_term_memory=True),
    )

    assert "context" in bundles
    assert "Recent surface + short follow-up" in runtime_prompt
    assert "R14_REFERENCE_BINDING" in runtime_prompt


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
    assert "count/existence->schedule_response_mode=count" in runtime_prompt
    assert "schedule_response_mode=count" in runtime_prompt


def test_money_move_fallback_examples_present() -> None:
    """Fallback money-move prompt should focus on active-flow, corrections, and scheduling."""
    runtime_prompt, _, bundles = _build_prompt(
        "make it 20k tomorrow 9am",
        "Active transfer flow",
        PlannerPromptSignals(active_flow_type="transfer"),
    )
    assert "money_move" in bundles
    assert "context" not in bundles
    assert "R09_CONTEXT_OVERRIDE" in runtime_prompt
    assert "R14_REFERENCE_BINDING" in runtime_prompt
    assert "R21_TRANSFER_SCHEDULING" in runtime_prompt
    assert 'Active transfer flow + "send it to her"' in runtime_prompt
    assert 'Active transfer flow + "make it 20k"' in runtime_prompt
    assert "Send 10k to Mum tomorrow 9am" in runtime_prompt
    assert "Send it to her every Friday" in runtime_prompt
    assert "explicit_split={Access:10000,GTB:10000}" in runtime_prompt
    assert "TARGETED EXAMPLES (CONTEXT)" not in runtime_prompt
    assert "Biko buy 3k airtime for my line mtn" in runtime_prompt


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
    assert "mixed_tx" in bundles
    assert "money_move" not in bundles
    assert "executor_coverage_guard" in bundles
    assert "R22_MIXED_MONEY_MOVE" in runtime_prompt
    assert "TARGETED EXAMPLES (MIXED_TX)" in runtime_prompt
    assert "send_money" in runtime_prompt
    assert "buy_airtime" in runtime_prompt
    assert "EXECUTOR COVERAGE GUARD" in runtime_prompt
    assert "clauses[]" in runtime_prompt
    assert "source_clause_index" in runtime_prompt
    assert "Never use text from one clause to fill another clause's slots." in runtime_prompt
    assert "Never drop a later read-only clause" in runtime_prompt
    assert "Apply clause decomposition semantically across supported languages." in runtime_prompt


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


def test_transactional_interrupt_replan_uses_transfer_only_bundle() -> None:
    runtime_prompt, _, bundles = _build_prompt(
        "change it to 20k",
        "Active transfer flow",
        PlannerPromptSignals(
            active_flow_type="transfer",
            pending_interrupt_kind="confirmation",
            forced_domain_owner="transfer",
            expected_transaction_executors=("transfer",),
        ),
    )
    assert "transfer_only" in bundles
    assert "money_move" not in bundles
    assert "context" not in bundles
    assert "executor_coverage_guard" not in bundles
    assert "TARGETED EXAMPLES (TRANSFER_ONLY)" in runtime_prompt
    assert "TARGETED EXAMPLES (MONEY_MOVE)" not in runtime_prompt
    assert "TARGETED EXAMPLES (CONTEXT)" not in runtime_prompt


def test_semantic_router_expected_executor_coverage_rules_present() -> None:
    """Semantic-router prompt should require all explicit mixed transaction executors."""
    assert "direct_context_answer" in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "domain_query" in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "domain_schedule" in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "domain_account" in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "planner_mixed" in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "expected_transaction_executors" in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "schedule_response_mode" in SEMANTIC_ROUTER_SYSTEM_PROMPT
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
    assert "Scheduled/recurring instruction management is not transaction-history query" in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert "never use this for scheduled/recurring instruction status or counts; use domain_schedule" in (
        SEMANTIC_ROUTER_SYSTEM_PROMPT
    )
    assert '"How many scheduled transactions are pending" -> domain_schedule' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"How many scheduled transactions are pending" -> domain_schedule, schedule_response_mode=count' in (
        SEMANTIC_ROUTER_SYSTEM_PROMPT
    )
    assert '"Do I have any pending scheduled transactions?" -> domain_schedule, schedule_response_mode=count' in (
        SEMANTIC_ROUTER_SYSTEM_PROMPT
    )
    assert '"Do i have any pending scheduled transsction" -> domain_schedule, schedule_response_mode=count' in (
        SEMANTIC_ROUTER_SYSTEM_PROMPT
    )
    assert '"How many scheduled transaction is pending" -> domain_schedule' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"Wetin be my scheduled payments" -> domain_schedule' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"Elo ni scheduled payments mi" -> domain_schedule' in SEMANTIC_ROUTER_SYSTEM_PROMPT
    assert '"Montre mes paiements programmés" -> domain_schedule' in SEMANTIC_ROUTER_SYSTEM_PROMPT
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
        "make it 20k tomorrow 9am",
        "Active transfer flow",
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
