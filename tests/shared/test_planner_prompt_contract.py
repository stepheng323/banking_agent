"""Contract tests for planner prompt guidance."""

from shared.services.task_planner import BASE_PLANNER_SYSTEM_PROMPT, INTERRUPT_ROUTER_SYSTEM_PROMPT


def test_beneficiary_reactive_save_requires_explicit_intent() -> None:
    """Prompt should prevent greeting text from being treated as save consent."""
    assert "BENEFICIARY SAVING (Reactive)" in BASE_PLANNER_SYSTEM_PROMPT
    assert "Treat greetings/check-ins/thanks" in BASE_PLANNER_SYSTEM_PROMPT
    expected = 'Context="Asked to save beneficiary", User="Hi" -> conversational, response_key=conversational.greeting'
    assert expected in BASE_PLANNER_SYSTEM_PROMPT


def test_context_read_fastpath_rules_present() -> None:
    """Prompt must define context-read fastpath + fallback contract."""
    assert "CONTEXT-READ FASTPATH V2" in BASE_PLANNER_SYSTEM_PROMPT
    assert 'primary_intent="conversational", tasks=[]' in BASE_PLANNER_SYSTEM_PROMPT
    assert "context_fastpath_subtype" in BASE_PLANNER_SYSTEM_PROMPT
    assert "beneficiary list (compact, max 5)" in BASE_PLANNER_SYSTEM_PROMPT
    assert "beneficiary name match preview (compact, max 3)" in BASE_PLANNER_SYSTEM_PROMPT
    assert "account_mandate_readiness_summary" in BASE_PLANNER_SYSTEM_PROMPT
    assert "account_linked_bank_existence_check" in BASE_PLANNER_SYSTEM_PROMPT
    assert "beneficiary_name_match_preview" in BASE_PLANNER_SYSTEM_PROMPT
    assert "flow_recap" in BASE_PLANNER_SYSTEM_PROMPT
    assert "flow_missing_requirements" in BASE_PLANNER_SYSTEM_PROMPT
    assert "FASTPATH FALLBACK (MANDATORY)" in BASE_PLANNER_SYSTEM_PROMPT
    assert "DO NOT guess. Route to worker with a domain task instead" in BASE_PLANNER_SYSTEM_PROMPT


def test_interrupt_status_query_contract_present() -> None:
    """Interrupt router prompt should include status-query decision + subtype contract."""
    assert (
        "decision: continue_flow | switch_intent | cancel | unclear | approve_flow | reject_flow | status_query"
        in INTERRUPT_ROUTER_SYSTEM_PROMPT
    )
    assert "status_query_type: recap | requirements | null" in INTERRUPT_ROUTER_SYSTEM_PROMPT
    assert "decision=status_query" in INTERRUPT_ROUTER_SYSTEM_PROMPT


def test_transfer_recipient_fidelity_rules_present() -> None:
    """Prompt must preserve typed transfer recipient names for resolver disambiguation."""
    assert "TRANSFER RECIPIENT FIDELITY (MANDATORY)" in BASE_PLANNER_SYSTEM_PROMPT
    assert 'keep recipient="tolu" even if User State has "Tolu Adebayo"' in BASE_PLANNER_SYSTEM_PROMPT
    assert "Resolver handles disambiguation; planner must preserve ambiguity." in BASE_PLANNER_SYSTEM_PROMPT
    assert 'Never set transfer recipient to instruction verbs/placeholders (for example: "send", "transfer", "pay", "recipient").' in BASE_PLANNER_SYSTEM_PROMPT
    assert '"I want to send 8k" -> transfer, t1 send_money amount=8000 (recipient omitted)' in BASE_PLANNER_SYSTEM_PROMPT


def test_multilingual_safety_rules_present() -> None:
    """Prompt should state language-agnostic routing and disambiguation boundaries."""
    assert "MULTILINGUAL SAFETY" in BASE_PLANNER_SYSTEM_PROMPT
    assert "Never rely on English-only keyword assumptions" in BASE_PLANNER_SYSTEM_PROMPT


def test_follow_up_referent_binding_rules_present() -> None:
    """Prompt should anchor vague follow-ups to the most recent discussed domain."""
    assert "FOLLOW-UP REFERENT BINDING (MANDATORY)" in BASE_PLANNER_SYSTEM_PROMPT
    assert "bind to the most recent domain from Recent Chat / Recent Domain Focus" in BASE_PLANNER_SYSTEM_PROMPT
    assert 'Recent Chat last turn was account_count answer, User="List them"' in BASE_PLANNER_SYSTEM_PROMPT
    assert 'Recent Chat last turn was beneficiary_count answer, User="List them"' in BASE_PLANNER_SYSTEM_PROMPT
