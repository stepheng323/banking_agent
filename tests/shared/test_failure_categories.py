from shared.services.failure_categories import classify_failure_category


def test_failure_category_maps_provider_unavailable() -> None:
    assert classify_failure_category(message="Provider down", context="provider") == "provider_unavailable"
    assert classify_failure_category(code="96", context="provider") == "provider_unavailable"


def test_failure_category_maps_provider_declined_and_funds() -> None:
    assert classify_failure_category(code="51", context="provider") == "insufficient_funds"
    assert classify_failure_category(message="Provider declined the request", context="provider") == "provider_declined"


def test_failure_category_maps_source_and_validation_errors() -> None:
    assert classify_failure_category(message="missing_source_account_id", context="execution") == "source_account"
    assert classify_failure_category(message="source_account_mandate_not_ready", context="execution") == "source_account"
    assert classify_failure_category(message="missing_recipient_account_details", context="execution") == "validation_error"
    assert classify_failure_category(message="invalid_transfer_amount", context="execution") == "validation_error"


def test_failure_category_maps_unknown_execution_exception() -> None:
    assert classify_failure_category(message="database exploded", context="execution") == "execution_error"
