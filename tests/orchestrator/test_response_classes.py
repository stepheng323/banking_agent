from apps.chat.src.agent.orchestrator.nodes.response_classes import (
    classify_read_only_response_class,
    is_surface_response_class,
)


def test_response_classifies_fact_status_turn() -> None:
    response_class = classify_read_only_response_class("Is my First Bank account ready?")
    assert response_class == "FACT_STATUS"
    assert is_surface_response_class(response_class) is False


def test_response_classifies_fact_count_turn() -> None:
    response_class = classify_read_only_response_class("How many linked accounts do I have?")
    assert response_class == "FACT_COUNT"
    assert is_surface_response_class(response_class) is False


def test_response_classifies_last_transaction_as_surface_detail() -> None:
    response_class = classify_read_only_response_class("Show my last transaction")
    assert response_class == "SURFACE_DETAIL"
    assert is_surface_response_class(response_class) is True


def test_response_classifies_linked_accounts_as_surface_list() -> None:
    response_class = classify_read_only_response_class("Show my linked accounts")
    assert response_class == "SURFACE_LIST"
    assert is_surface_response_class(response_class) is True


def test_response_classifies_question_form_linked_accounts_as_surface_list() -> None:
    response_class = classify_read_only_response_class("What linked accounts do I have?")
    assert response_class == "SURFACE_LIST"
    assert is_surface_response_class(response_class) is True


def test_response_classifies_query_more_as_surface_pagination() -> None:
    response_class = classify_read_only_response_class(
        "More",
        loaded_context={"language": "en"},
        query_session_snapshot={"session_active": True},
    )
    assert response_class == "SURFACE_PAGINATED"
    assert is_surface_response_class(response_class) is True


def test_response_classifies_query_receipt_as_surface_actionable() -> None:
    response_class = classify_read_only_response_class(
        "receipt",
        loaded_context={"language": "en"},
        query_session_snapshot={"session_active": True},
    )
    assert response_class == "SURFACE_ACTIONABLE"
    assert is_surface_response_class(response_class) is True


def test_response_does_not_heuristically_classify_show_me_active_query_followup() -> None:
    response_class = classify_read_only_response_class(
        "show me",
        loaded_context={"language": "en"},
        query_session_snapshot={"session_active": True},
    )
    assert response_class is None
