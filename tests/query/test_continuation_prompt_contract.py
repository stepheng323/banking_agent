from apps.core.src.agent.graphs.query.prompts.main import CONTINUATION_CLASSIFIER_PROMPT


def test_continuation_prompt_includes_question_form_filter_examples() -> None:
    assert 'User: "is there any credit?"' in CONTINUATION_CLASSIFIER_PROMPT
    assert '-> {{"continuation_type": "filter_delta", "filters": {{"transaction_type": "credit"}}}}' in (
        CONTINUATION_CLASSIFIER_PROMPT
    )
    assert 'User: "how about debits?"' in CONTINUATION_CLASSIFIER_PROMPT
    assert '-> {{"continuation_type": "filter_delta", "filters": {{"transaction_type": "debit"}}}}' in (
        CONTINUATION_CLASSIFIER_PROMPT
    )
