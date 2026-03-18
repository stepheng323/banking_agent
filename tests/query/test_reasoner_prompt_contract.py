from apps.core.src.agent.graphs.query.prompts.main import QUERY_SEMANTIC_REASONER_PROMPT


def test_query_reasoner_prompt_includes_unified_decisions() -> None:
    assert "- fresh_query" in QUERY_SEMANTIC_REASONER_PROMPT
    assert "- clarification_answer" in QUERY_SEMANTIC_REASONER_PROMPT
    assert "- reinterpret_query" in QUERY_SEMANTIC_REASONER_PROMPT
    assert "- continuation" in QUERY_SEMANTIC_REASONER_PROMPT
    assert "- new_query" in QUERY_SEMANTIC_REASONER_PROMPT
    assert "- end_session" in QUERY_SEMANTIC_REASONER_PROMPT


def test_query_reasoner_prompt_covers_latest_item_shape_for_last() -> None:
    assert '"How much did I send to mum last" -> latest matching transaction shape' in QUERY_SEMANTIC_REASONER_PROMPT


def test_query_reasoner_prompt_covers_active_result_fact_followups() -> None:
    assert 'factual questions about the currently displayed single item should also use continuation_type="drill_down"' in (
        QUERY_SEMANTIC_REASONER_PROMPT
    )
    assert '- status' in QUERY_SEMANTIC_REASONER_PROMPT
    assert '- amount' in QUERY_SEMANTIC_REASONER_PROMPT
    assert '- recipient' in QUERY_SEMANTIC_REASONER_PROMPT
    assert '- bank' in QUERY_SEMANTIC_REASONER_PROMPT
    assert '- date' in QUERY_SEMANTIC_REASONER_PROMPT


def test_query_reasoner_prompt_tightens_active_result_answer_fact_boundary() -> None:
    assert 'Use drill_down_action="answer_fact" only when the user is clearly referring to the currently displayed item' in (
        QUERY_SEMANTIC_REASONER_PROMPT
    )
    assert '"Have I sent money today?" -> fresh_query or new_query, not answer_fact.' in QUERY_SEMANTIC_REASONER_PROMPT
    assert (
        '"How much have I sent to mum this week?" -> fresh_query or new_query, not answer_fact.'
        in QUERY_SEMANTIC_REASONER_PROMPT
    )


def test_query_reasoner_prompt_covers_conversational_reactions_in_active_sessions() -> None:
    assert 'continuation_type="conversational"' in QUERY_SEMANTIC_REASONER_PROMPT
    assert "response_text" in QUERY_SEMANTIC_REASONER_PROMPT
    assert "contextual_hint" in QUERY_SEMANTIC_REASONER_PROMPT
    assert "Do not trigger pagination, expand, drill-down, or any other mutation for conversational reactions." in (
        QUERY_SEMANTIC_REASONER_PROMPT
    )


def test_query_reasoner_prompt_requires_followup_intent_for_continuations() -> None:
    assert "`followup_intent` is required for every continuation decision" in QUERY_SEMANTIC_REASONER_PROMPT
    assert 'continuation_type="unclear" and followup_intent="none"' in QUERY_SEMANTIC_REASONER_PROMPT


def test_query_reasoner_prompt_defines_legal_followup_intent_combinations() -> None:
    assert 'continuation_type="show_more"' in QUERY_SEMANTIC_REASONER_PROMPT
    assert 'followup_intent="continue_pagination"' in QUERY_SEMANTIC_REASONER_PROMPT
    assert 'followup_intent="refine_existing"' in QUERY_SEMANTIC_REASONER_PROMPT
    assert 'continuation_type="time_delta"' in QUERY_SEMANTIC_REASONER_PROMPT
    assert 'followup_intent="replace_scope"' in QUERY_SEMANTIC_REASONER_PROMPT
    assert 'continuation_type="filter_delta"' in QUERY_SEMANTIC_REASONER_PROMPT
