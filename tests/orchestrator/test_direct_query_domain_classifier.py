from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.direct_domains import _is_query_domain_request
from apps.chat.src.agent.orchestrator.workflows.gate.classifiers.transaction_intents import (
    classify_obvious_transfer_request,
)


def test_affordability_probe_is_query_not_transfer() -> None:
    assert _is_query_domain_request("Can I send 100k?")
    assert classify_obvious_transfer_request("Can I send 100k?") is None


def test_true_transfer_with_recipient_still_routes_as_transfer() -> None:
    assert classify_obvious_transfer_request("Send 100k to Tolu") == "fresh_transfer_command"


def test_transcript_query_shapes_are_direct_query_requests() -> None:
    assert _is_query_domain_request("How much came in this month")
    assert _is_query_domain_request("Who sent me the most money this month")
    assert _is_query_domain_request("Where did my money go this month")
