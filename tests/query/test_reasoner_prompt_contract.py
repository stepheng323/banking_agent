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
