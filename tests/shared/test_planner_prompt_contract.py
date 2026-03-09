"""Contract tests for planner prompt guidance."""

from shared.services.task_planner import (
    INTERRUPT_ROUTER_SYSTEM_PROMPT,
    PLANNER_PROMPT_BASELINE_RESULT,
    PLANNER_RULE_ATOMS,
    TURN_ROUTER_SYSTEM_PROMPT,
    PlannerPromptBuildInput,
    PlannerPromptSignals,
    build_planner_system_prompt,
)


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
    assert PLANNER_RULE_ATOMS["R12_BENEFICIARY_HANDLING"] in runtime_prompt
    assert 'Asked to save beneficiary + "Hi" -> conversational' in runtime_prompt


def test_context_read_fastpath_rules_present() -> None:
    """Prompt must define context-read fastpath + fallback contract."""
    runtime_prompt, _, _ = _build_prompt("Which account is default?", "User State has default account")
    assert PLANNER_RULE_ATOMS["R16_FASTPATH_CONTEXT_READ"] in runtime_prompt
    assert PLANNER_RULE_ATOMS["R17_FASTPATH_FALLBACK"] in runtime_prompt
    assert PLANNER_RULE_ATOMS["R18_FASTPATH_SUBTYPE"] in runtime_prompt
    assert "context_fastpath_subtype" in runtime_prompt
    assert "account_mandate_readiness_summary" in runtime_prompt
    assert "account_linked_bank_existence_check" in runtime_prompt
    assert "beneficiary_name_match_preview" in runtime_prompt
    assert "flow_recap" in runtime_prompt
    assert "flow_missing_requirements" in runtime_prompt


def test_interrupt_status_query_contract_present() -> None:
    """Interrupt router prompt should include status-query decision + subtype contract."""
    assert (
        "decision: continue_flow | switch_intent | cancel | unclear | approve_flow | reject_flow | status_query"
        in INTERRUPT_ROUTER_SYSTEM_PROMPT
    )
    assert "status_query_type: recap | requirements | null" in INTERRUPT_ROUTER_SYSTEM_PROMPT
    assert "decision=status_query" in INTERRUPT_ROUTER_SYSTEM_PROMPT
    assert '"make it 20k"' in INTERRUPT_ROUTER_SYSTEM_PROMPT
    assert "target_intent=null" in INTERRUPT_ROUTER_SYSTEM_PROMPT


def test_transfer_recipient_fidelity_rules_present() -> None:
    """Prompt must preserve typed transfer recipient names for resolver disambiguation."""
    runtime_prompt, _, bundles = _build_prompt(
        "Send 5k to tolu",
        "None",
        PlannerPromptSignals(active_flow_type="transfer"),
    )
    assert "money_move" in bundles
    assert PLANNER_RULE_ATOMS["R19_TRANSFER_FIDELITY"] in runtime_prompt
    assert "send_money amount=8000" in runtime_prompt
    assert "recipient omitted" in runtime_prompt


def test_multilingual_safety_rules_present() -> None:
    """Prompt should state language-agnostic routing and disambiguation boundaries."""
    runtime_prompt, _, _ = _build_prompt("How far", "None")
    assert PLANNER_RULE_ATOMS["R23_MULTILINGUAL_SAFETY"] in runtime_prompt


def test_follow_up_referent_binding_rules_present() -> None:
    """Prompt should anchor vague follow-ups to the most recent discussed domain."""
    runtime_prompt, _, bundles = _build_prompt(
        "Send 10k to her",
        "Recent Chat last turn was beneficiary_count answer",
        PlannerPromptSignals(recent_domain_focus="query", active_flow_type="query"),
    )
    assert "query" in bundles
    assert PLANNER_RULE_ATOMS["R14_REFERENCE_BINDING"] in runtime_prompt
    assert 'Recent Chat account_count + "List them"' in runtime_prompt
    assert 'Recent Chat beneficiary_count + "List them"' in runtime_prompt


def test_transfer_pronoun_reference_continuity_rules_present() -> None:
    """Prompt should preserve beneficiary pronoun continuity with reference semantics."""
    runtime_prompt, _, _ = _build_prompt(
        "Send 10k to her",
        "Recent Chat last turn listed beneficiaries",
        PlannerPromptSignals(active_flow_type="transfer"),
    )
    assert PLANNER_RULE_ATOMS["R14_REFERENCE_BINDING"] in runtime_prompt
    assert '{"selector":"previous"}' in runtime_prompt
    assert '{"selector":"index","index":N}' in runtime_prompt


def test_transfer_scheduling_rules_present() -> None:
    """Prompt should include schedule/recurring transfer action contracts."""
    runtime_prompt, _, _ = _build_prompt(
        "Send 10k to Mum tomorrow 9am",
        "None",
        PlannerPromptSignals(active_flow_type="transfer"),
    )
    assert PLANNER_RULE_ATOMS["R21_TRANSFER_SCHEDULING"] in runtime_prompt
    assert "schedule_transfer" in runtime_prompt
    assert "recurring_transfer" in runtime_prompt
    assert "list_scheduled_transfers" in runtime_prompt
    assert "cancel_scheduled_transfer" in runtime_prompt


def test_mixed_money_move_coverage_rules_present() -> None:
    """Prompt should force full task coverage for explicit mixed money-move requests."""
    runtime_prompt, _, bundles = _build_prompt(
        "Send 10k to Mum and buy me 5k airtime",
        "None",
        PlannerPromptSignals(expected_transaction_executors=("transfer", "airtime")),
    )
    assert "money_move" in bundles
    assert "executor_coverage_guard" in bundles
    assert PLANNER_RULE_ATOMS["R22_MIXED_MONEY_MOVE"] in runtime_prompt
    assert "TARGETED EXAMPLES (MONEY_MOVE)" in runtime_prompt
    assert "send_money" in runtime_prompt
    assert "buy_airtime" in runtime_prompt
    assert "EXECUTOR COVERAGE GUARD" in runtime_prompt


def test_turn_router_expected_executor_coverage_rules_present() -> None:
    """Turn-router prompt should require all explicit mixed transaction executors."""
    assert "expected_transaction_executors" in TURN_ROUTER_SYSTEM_PROMPT
    assert "explicit mixed transaction requests" in TURN_ROUTER_SYSTEM_PROMPT
    assert "include every mentioned executor" in TURN_ROUTER_SYSTEM_PROMPT
    assert '["transfer","airtime"]' in TURN_ROUTER_SYSTEM_PROMPT


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
    assert "## COMPILED RULE ATOMS" in runtime_prompt
    assert "TARGETED EXAMPLES (COMMON)" in runtime_prompt
    assert "TARGETED EXAMPLES (MONEY_MOVE)" not in runtime_prompt
    assert "TARGETED EXAMPLES (QUERY)" not in runtime_prompt
    assert "TARGETED EXAMPLES (CONTEXT)" not in runtime_prompt
    assert "schema" in profile
    assert "ex_common" in profile
    assert not bundles
    assert set(expanded_bundles) == {"money_move", "query", "context", "executor_coverage_guard"}
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
    assert len(generic_prompt) <= 5227
    assert len(expanded_prompt) <= 6981


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


def test_runtime_planner_prompt_adds_query_examples_when_relevant() -> None:
    """Runtime prompt should include query examples and query-specific rule atoms for query turns."""
    runtime_prompt, profile, bundles = _build_prompt(
        "How much did I spend last week?",
        "None",
        PlannerPromptSignals(query_session_active=True, recent_domain_focus="query"),
    )
    assert "query" in bundles
    assert "TARGETED EXAMPLES (QUERY)" in runtime_prompt
    assert "R13_QUERY_CONTINUATION" in runtime_prompt
    assert "ex_query" in profile


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
