from apps.chat.src.agent.graphs.query.prompts.main import (
    QUERY_PARSER_PROMPT,
    QUERY_SEMANTIC_REASONER_CONTEXT,
    QUERY_SEMANTIC_REASONER_SYSTEM,
)


def test_query_reasoner_prompt_includes_unified_decisions() -> None:
    assert "fresh_query" in QUERY_SEMANTIC_REASONER_SYSTEM
    assert "clarification_answer" in QUERY_SEMANTIC_REASONER_SYSTEM
    assert "reinterpret_query" in QUERY_SEMANTIC_REASONER_SYSTEM
    assert "continuation" in QUERY_SEMANTIC_REASONER_SYSTEM
    assert "new_query" in QUERY_SEMANTIC_REASONER_SYSTEM
    assert "end_session" in QUERY_SEMANTIC_REASONER_SYSTEM


def test_query_reasoner_prompt_covers_all_continuation_types() -> None:
    for ct in (
        "show_more",
        "show_evidence",
        "grouped_total_followup",
        "time_delta",
        "filter_delta",
        "expand",
        "conversational",
        "coverage",
        "explain_aggregate_scope",
        "drill_down",
        "recipient_drill_down",
        "aggregate",
        "unclear",
    ):
        assert ct in QUERY_SEMANTIC_REASONER_SYSTEM, f"Missing continuation_type: {ct}"


def test_query_reasoner_prompt_covers_followup_intents() -> None:
    for intent in ("continue_pagination", "previous_pagination", "refine_existing", "replace_scope", "none"):
        assert intent in QUERY_SEMANTIC_REASONER_SYSTEM, f"Missing followup_intent: {intent}"


def test_query_reasoner_prompt_covers_fact_fields() -> None:
    for field in ("status", "amount", "recipient", "bank", "date"):
        assert field in QUERY_SEMANTIC_REASONER_SYSTEM, f"Missing fact_field: {field}"


def test_query_reasoner_prompt_covers_query_operations() -> None:
    for op in (
        "sum_transactions",
        "breakdown_transactions",
        "summarize_beneficiaries",
        "list_transactions",
        "compare_periods",
    ):
        assert op in QUERY_SEMANTIC_REASONER_SYSTEM, f"Missing query_operation: {op}"


def test_query_reasoner_prompt_covers_frame_grounding() -> None:
    assert "referenced_frame_ids" in QUERY_SEMANTIC_REASONER_SYSTEM
    assert "grounded_operation" in QUERY_SEMANTIC_REASONER_SYSTEM
    assert "answer_mode" in QUERY_SEMANTIC_REASONER_SYSTEM
    assert "memory_answer" in QUERY_SEMANTIC_REASONER_SYSTEM
    assert "grounded_query" in QUERY_SEMANTIC_REASONER_SYSTEM
    assert "compare_frames" in QUERY_SEMANTIC_REASONER_SYSTEM


def test_query_reasoner_prompt_covers_conversational_reactions() -> None:
    assert "conversational" in QUERY_SEMANTIC_REASONER_SYSTEM
    assert "response_text" in QUERY_SEMANTIC_REASONER_SYSTEM


def test_query_reasoner_prompt_covers_dismissive_end_session() -> None:
    assert "dismissive" in QUERY_SEMANTIC_REASONER_SYSTEM


def test_query_reasoner_prompt_covers_extraction_rules() -> None:
    assert "extraction" in QUERY_SEMANTIC_REASONER_SYSTEM
    assert "query_operation" in QUERY_SEMANTIC_REASONER_SYSTEM
    assert "result_reference" in QUERY_SEMANTIC_REASONER_SYSTEM


def test_query_reasoner_prompt_mentions_multilingual_support() -> None:
    assert "Nigerian Pidgin" in QUERY_SEMANTIC_REASONER_SYSTEM
    assert "Yoruba" in QUERY_SEMANTIC_REASONER_SYSTEM


def test_query_parser_prompt_mentions_bounded_query_operations() -> None:
    assert "QUERY OPERATION" in QUERY_PARSER_PROMPT
    assert "list_transactions" in QUERY_PARSER_PROMPT
    assert "search_single_transaction" in QUERY_PARSER_PROMPT
    assert "compare_periods" in QUERY_PARSER_PROMPT


def test_query_parser_prompt_covers_recipient_summary_and_ranking() -> None:
    assert "beneficiary_summary" in QUERY_PARSER_PROMPT
    assert "sort_by" in QUERY_PARSER_PROMPT
    assert "grouped_summary" in QUERY_PARSER_PROMPT
    assert "who I send money give this month" in QUERY_PARSER_PROMPT
    assert "tani mo ran owo si ni osu yi" in QUERY_PARSER_PROMPT
    assert "onye ka m zigara ego n'onwa a" in QUERY_PARSER_PROMPT
    assert "wa na tura wa kudi a wannan watan" in QUERY_PARSER_PROMPT
    assert "qui ai je envoye de l argent ce mois ci" in QUERY_PARSER_PROMPT


def test_query_parser_prompt_covers_time_normalization() -> None:
    assert "explicit" in QUERY_PARSER_PROMPT
    assert "this_week" in QUERY_PARSER_PROMPT
    assert "last_week" in QUERY_PARSER_PROMPT
    assert "this_month" in QUERY_PARSER_PROMPT
    assert "last_month" in QUERY_PARSER_PROMPT
    assert "days_back" in QUERY_PARSER_PROMPT
    assert 'Unscoped "when last did' in QUERY_SEMANTIC_REASONER_SYSTEM


def test_query_parser_prompt_covers_aggregation_rules() -> None:
    assert "largest" in QUERY_PARSER_PROMPT
    assert "smallest" in QUERY_PARSER_PROMPT
    assert "breakdown" in QUERY_PARSER_PROMPT
    assert "transaction_type" in QUERY_PARSER_PROMPT


def test_query_parser_prompt_covers_multilingual() -> None:
    assert "Nigerian Pidgin" in QUERY_PARSER_PROMPT
    assert "Yoruba" in QUERY_PARSER_PROMPT


def test_query_parser_prompt_uses_positive_output_contract() -> None:
    assert "OUTPUT CONTRACT" in QUERY_PARSER_PROMPT
    assert "Return only these fields" in QUERY_PARSER_PROMPT
    assert "request_shape" in QUERY_PARSER_PROMPT
    assert "fact_query_kind" in QUERY_PARSER_PROMPT
    assert "answer_fact_field" in QUERY_PARSER_PROMPT
    assert "existence" in QUERY_PARSER_PROMPT
    assert "reference" in QUERY_PARSER_PROMPT
    assert "Do not rely on raw wording for recovery" in QUERY_PARSER_PROMPT
    assert "The runtime derives `query_operation`" in QUERY_PARSER_PROMPT
    assert "REQUESTED CAPABILITIES" not in QUERY_PARSER_PROMPT
    assert "AMBIGUITIES" not in QUERY_PARSER_PROMPT


def test_query_prompts_require_typed_multilingual_fact_semantics() -> None:
    assert "Runtime validation will not infer these fields from raw text" in QUERY_SEMANTIC_REASONER_SYSTEM
    assert "when did I last send mum money" in QUERY_PARSER_PROMPT
    assert "who send me 500k last week" in QUERY_PARSER_PROMPT
    assert "bank wo ni mo lo fun last transfer" in QUERY_PARSER_PROMPT
    assert "nawa ne bank din last transaction dina" in QUERY_PARSER_PROMPT
    assert "ole ego ka m zigara tolu ikpeazu" in QUERY_PARSER_PROMPT
    assert "quelle banque pour ma derniere transaction" in QUERY_PARSER_PROMPT
    assert "did I send money to mum this month" in QUERY_PARSER_PROMPT
    assert "what was the reference for that payment" in QUERY_PARSER_PROMPT


def test_query_prompts_put_dynamic_message_late_for_cache_reuse() -> None:
    assert QUERY_PARSER_PROMPT.index("USER MESSAGE") > QUERY_PARSER_PROMPT.index("OUTPUT CONTRACT")
    assert QUERY_SEMANTIC_REASONER_CONTEXT.index("USER MESSAGE") > QUERY_SEMANTIC_REASONER_CONTEXT.index(
        "CURRENT QUERY SNAPSHOT"
    )
