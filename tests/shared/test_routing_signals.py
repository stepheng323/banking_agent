import pytest

from apps.chat.src.agent.shared.routing_signals import looks_like_support_problem_statement


@pytest.mark.parametrize(
    "message",
    [
        "My last transaction failed",
        "I was debited but they didn't receive it",
        "Retry that failed transfer",
        "What happened to my complaint?",
    ],
)
def test_support_problem_signal_matches_problem_statements(message: str) -> None:
    assert looks_like_support_problem_statement(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "Show failed transactions",
        "Show refund transactions",
        "What is the status of my last transaction?",
        "How many failed transfers did I have?",
    ],
)
def test_support_problem_signal_preserves_explicit_query_shapes(message: str) -> None:
    assert looks_like_support_problem_statement(message) is False
