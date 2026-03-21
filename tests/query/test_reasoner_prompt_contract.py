from apps.core.src.agent.graphs.query.prompts.main import QUERY_PARSER_PROMPT, QUERY_SEMANTIC_REASONER_PROMPT


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
    assert '"No transaction yesterday?" after showing one last transaction -> time_delta replace_scope, not answer_fact.' in (
        QUERY_SEMANTIC_REASONER_PROMPT
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
    assert "The runtime resolves the new time window from the user message with the full query parser." in (
        QUERY_SEMANTIC_REASONER_PROMPT
    )
    assert "You may still include `time_range`, `time_period`, or `extraction` when useful, but runtime correctness must not depend on them." in (
        QUERY_SEMANTIC_REASONER_PROMPT
    )


def test_query_reasoner_prompt_defines_legal_followup_intent_combinations() -> None:
    assert 'continuation_type="show_more"' in QUERY_SEMANTIC_REASONER_PROMPT
    assert 'followup_intent="continue_pagination"' in QUERY_SEMANTIC_REASONER_PROMPT
    assert 'followup_intent="refine_existing"' in QUERY_SEMANTIC_REASONER_PROMPT
    assert 'continuation_type="time_delta"' in QUERY_SEMANTIC_REASONER_PROMPT
    assert 'followup_intent="replace_scope"' in QUERY_SEMANTIC_REASONER_PROMPT
    assert 'continuation_type="filter_delta"' in QUERY_SEMANTIC_REASONER_PROMPT
    assert "referenced_frame_ids" in QUERY_SEMANTIC_REASONER_PROMPT
    assert "grounded_operation" in QUERY_SEMANTIC_REASONER_PROMPT
    assert "answer_mode" in QUERY_SEMANTIC_REASONER_PROMPT


def test_query_reasoner_prompt_covers_weekly_scope_replacement_transcript() -> None:
    assert '"How much did I spend this week" -> fresh/new query with explicit this_week aggregate spend shape' in (
        QUERY_SEMANTIC_REASONER_PROMPT
    )
    assert '"How much did I send to mum this week" -> fresh/new query with recipient + debit + this_week aggregate spend shape' in (
        QUERY_SEMANTIC_REASONER_PROMPT
    )
    assert '"Show them" or "show me" after that summary -> continuation_type="show_more" and followup_intent="refine_existing"' in (
        QUERY_SEMANTIC_REASONER_PROMPT
    )
    assert '"Only today", "Only this week\'s", or "for last month only" after that summary/list -> continuation_type="time_delta" and followup_intent="replace_scope"; runtime resolves the new time window from the user message' in (
        QUERY_SEMANTIC_REASONER_PROMPT
    )
    assert '"What about last week", "what about yesterday", or "and last month?" after that summary/list -> continuation_type="time_delta" and followup_intent="replace_scope"; runtime resolves the new time window from the user message' in (
        QUERY_SEMANTIC_REASONER_PROMPT
    )
    assert '"What about yesterday?" after showing the last transaction -> continuation_type="time_delta" and followup_intent="replace_scope"; preserve the singular/latest shape in the new time window' in (
        QUERY_SEMANTIC_REASONER_PROMPT
    )
    assert '"more" or "next page" on that list -> continuation_type="show_more" and followup_intent="continue_pagination"' in (
        QUERY_SEMANTIC_REASONER_PROMPT
    )
    assert '"How much total", "what\'s the total", or "sum it up" after that transaction list/summary -> continuation_type="aggregate" and followup_intent="refine_existing"; preserve the current scope' in (
        QUERY_SEMANTIC_REASONER_PROMPT
    )
    assert '"total for mum" after that transaction list/summary -> continuation_type="aggregate" and followup_intent="refine_existing"; keep the active time scope and narrow recipient filter' in (
        QUERY_SEMANTIC_REASONER_PROMPT
    )
    assert '"wetin be total", "nawa be total", or "lapapo meloo" after that transaction list/summary -> continuation_type="aggregate" and followup_intent="refine_existing"' in (
        QUERY_SEMANTIC_REASONER_PROMPT
    )
    assert '"What my income this month" or "what\'s my income this month" after that credit transaction list -> continuation_type="aggregate" and followup_intent="refine_existing"; treat income as total credit inflows for the active month scope' in (
        QUERY_SEMANTIC_REASONER_PROMPT
    )
    assert '"I mean my income this month" or "total income then" after that credit transaction list -> continuation_type="aggregate" and followup_intent="refine_existing"; keep the active credit scope and recover from the repair phrasing' in (
        QUERY_SEMANTIC_REASONER_PROMPT
    )
    assert 'explicit salary-only asks like "salary this month" are narrower than generic income and should only narrow when the user clearly says salary/earnings/paycheck' in (
        QUERY_SEMANTIC_REASONER_PROMPT
    )
    assert '"Show my credit transactions this month" after a spending summary -> decision="new_query" with a fresh credit/list extraction, not continuation_type="time_delta"' in (
        QUERY_SEMANTIC_REASONER_PROMPT
    )
    assert '"Show my debit transactions this month" after a credit summary -> decision="new_query" with a fresh debit/list extraction' in (
        QUERY_SEMANTIC_REASONER_PROMPT
    )


def test_query_parser_prompt_covers_weekly_aggregate_and_possessive_period_phrasing() -> None:
    assert '"How much did I spend today" → explicit period today' in QUERY_PARSER_PROMPT
    assert '"today\'s spending" → explicit period today' in QUERY_PARSER_PROMPT
    assert '"for yesterday only" → explicit period yesterday' in QUERY_PARSER_PROMPT
    assert '"How much did I spend this week" → explicit period this_week' in QUERY_PARSER_PROMPT
    assert '"this week\'s spending" → explicit period this_week' in QUERY_PARSER_PROMPT
    assert '"just this week" → explicit period this_week' in QUERY_PARSER_PROMPT
    assert '"How much did I spend last week" → explicit period last_week' in QUERY_PARSER_PROMPT
    assert '"last week\'s transfers" → explicit period last_week' in QUERY_PARSER_PROMPT
    assert '"How much did I spend this month" → explicit period this_month' in QUERY_PARSER_PROMPT
    assert '"this month\'s transactions" → explicit period this_month' in QUERY_PARSER_PROMPT
    assert '"only this month" → explicit period this_month' in QUERY_PARSER_PROMPT
    assert '"How much did I spend last month" → explicit period last_month' in QUERY_PARSER_PROMPT


def test_query_reasoner_prompt_covers_generic_timeframe_scope_replacement() -> None:
    assert '"How much did I spend today" -> fresh/new query with explicit today aggregate spend shape' in (
        QUERY_SEMANTIC_REASONER_PROMPT
    )
    assert '"How much did I spend last month" -> fresh/new query with explicit last_month aggregate spend shape' in (
        QUERY_SEMANTIC_REASONER_PROMPT
    )
    assert '"Only today", "Only this week\'s", or "for last month only" after that summary/list -> continuation_type="time_delta" and followup_intent="replace_scope"; runtime resolves the new time window from the user message' in (
        QUERY_SEMANTIC_REASONER_PROMPT
    )
    assert '"What about last week", "what about yesterday", or "and last month?" after that summary/list -> continuation_type="time_delta" and followup_intent="replace_scope"; runtime resolves the new time window from the user message' in (
        QUERY_SEMANTIC_REASONER_PROMPT
    )


def test_query_reasoner_prompt_covers_recent_frame_grounding_examples() -> None:
    assert 'User: "what\'s the difference between the 2 weeks"' in QUERY_SEMANTIC_REASONER_PROMPT
    assert 'User: "compare both"' in QUERY_SEMANTIC_REASONER_PROMPT
    assert 'User: "which one was higher"' in QUERY_SEMANTIC_REASONER_PROMPT
    assert 'User: "what about the first one"' in QUERY_SEMANTIC_REASONER_PROMPT
    assert 'User: "show transactions for that one"' in QUERY_SEMANTIC_REASONER_PROMPT
