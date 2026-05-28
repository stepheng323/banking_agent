from shared.transaction_runtime import provider_results


def test_provider_reference_reads_nested_provider_payloads() -> None:
    result = {"raw_response": {"provider_reference": " ref-123 "}}

    assert provider_results.provider_reference(result) == "ref-123"


def test_provider_status_treats_pending_message_as_processing() -> None:
    result = {"message": "Bill payment is pending"}

    assert provider_results.provider_status(result) == "pending"
    assert provider_results.provider_status_is_processing(result) is True


def test_provider_error_helpers_use_first_available_value() -> None:
    result = {"error": "Declined", "responseCode": "55"}

    assert provider_results.provider_error_message(result, "fallback") == "Declined"
    assert provider_results.provider_error_code(result) == "55"
