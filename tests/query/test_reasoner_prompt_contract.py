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
